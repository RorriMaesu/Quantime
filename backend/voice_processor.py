# backend/voice_processor.py
import os
import platform
import logging

logger = logging.getLogger("quantime.voice_processor")

# On Windows, when run under background environments (like Task Scheduler/VBScript),
# user environment variables (such as custom HF_HOME or OLLAMA_MODELS) may be missing.
# We restore them directly from the registry to ensure offline caches are resolved correctly.
if platform.system() == "Windows":
    try:
        import winreg
        for hkey, subkey in [
            (winreg.HKEY_CURRENT_USER, "Environment"),
            (winreg.HKEY_LOCAL_MACHINE, r"System\CurrentControlSet\Control\Session Manager\Environment")
        ]:
            try:
                with winreg.OpenKey(hkey, subkey, 0, winreg.KEY_READ) as key:
                    i = 0
                    while True:
                        name, value, val_type = winreg.EnumValue(key, i)
                        if name in ("HF_HOME", "HF_HUB_CACHE", "OLLAMA_MODELS"):
                            if name not in os.environ:
                                expanded_value = os.path.expandvars(str(value))
                                os.environ[name] = expanded_value
                                logger.info(f"Restored registry env: {name}={expanded_value}")
                        i += 1
            except OSError:
                pass
    except Exception as e:
        logger.warning(f"Failed to load user environment registry: {e}")

os.environ["HF_HUB_OFFLINE"] = "1"

import io
import wave
import logging
import numpy as np
import sys


# Lazy-loaded TTS Pipelines to prevent boot-up delays
_kokoro_pipeline = None
_vibevoice_model = None
_vibevoice_processor = None

def get_tts_pipeline():
    global _kokoro_pipeline
    if _kokoro_pipeline is None or _kokoro_pipeline == "mock":
        try:
            from kokoro import KPipeline
            logger.info("Initializing Kokoro-82M TTS Pipeline...")
            _kokoro_pipeline = KPipeline(lang_code='a') # 'a' for American English
            logger.info("Kokoro TTS Pipeline loaded successfully.")
        except Exception as e:
            logger.error(f"Failed to load Kokoro-82M TTS: {e}. Falling back to mock TTS.")
            _kokoro_pipeline = "mock"
    return _kokoro_pipeline

def get_vibevoice_pipeline():
    global _vibevoice_model, _vibevoice_processor
    if _vibevoice_model is None:
        try:
            # Ensure VibeVoice source directory is in the path
            src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "vibevoice_src"))
            if src_path not in sys.path:
                sys.path.append(src_path)
            
            import torch
            from vibevoice.modular.modeling_vibevoice_streaming_inference import VibeVoiceStreamingForConditionalGenerationInference
            from vibevoice.processor.vibevoice_streaming_processor import VibeVoiceStreamingProcessor
            
            model_path = "microsoft/VibeVoice-Realtime-0.5B"
            device = "cuda" if torch.cuda.is_available() else "cpu"
            logger.info(f"Initializing VibeVoice Realtime TTS on {device}...")
            
            # 1. Load the Processor
            try:
                _vibevoice_processor = VibeVoiceStreamingProcessor.from_pretrained(model_path, local_files_only=True)
            except Exception:
                logger.info("VibeVoice processor not found in local cache. Attempting to fetch from HF Hub...")
                old_offline = os.environ.get("HF_HUB_OFFLINE")
                if old_offline:
                    del os.environ["HF_HUB_OFFLINE"]
                try:
                    _vibevoice_processor = VibeVoiceStreamingProcessor.from_pretrained(model_path)
                finally:
                    if old_offline:
                        os.environ["HF_HUB_OFFLINE"] = old_offline
            
            # Decide dtype & attention implementation
            if device == "cuda":
                load_dtype = torch.bfloat16
                attn_impl = "sdpa"
            else:
                load_dtype = torch.float32
                attn_impl = "sdpa"
            
            # 2. Load the Model
            try:
                _vibevoice_model = VibeVoiceStreamingForConditionalGenerationInference.from_pretrained(
                    model_path,
                    torch_dtype=load_dtype,
                    device_map=device,
                    attn_implementation=attn_impl,
                    local_files_only=True
                )
            except Exception as e:
                logger.info(f"VibeVoice model not loaded locally ({e}). Attempting online download...")
                old_offline = os.environ.get("HF_HUB_OFFLINE")
                if old_offline:
                    del os.environ["HF_HUB_OFFLINE"]
                try:
                    _vibevoice_model = VibeVoiceStreamingForConditionalGenerationInference.from_pretrained(
                        model_path,
                        torch_dtype=load_dtype,
                        device_map=device,
                        attn_implementation=attn_impl
                    )
                except Exception as online_err:
                    # If GPU model fails, retry on CPU as fallback
                    logger.warning(f"VibeVoice online load failed ({online_err}). Retrying on CPU fallback...")
                    _vibevoice_model = VibeVoiceStreamingForConditionalGenerationInference.from_pretrained(
                        model_path,
                        torch_dtype=torch.float32,
                        device_map="cpu",
                        attn_implementation="sdpa"
                    )
                finally:
                    if old_offline:
                        os.environ["HF_HUB_OFFLINE"] = old_offline
                        
            _vibevoice_model.eval()
            logger.info("VibeVoice Realtime TTS loaded successfully.")
        except Exception as e:
            logger.error(f"Failed to load VibeVoice: {e}.")
            _vibevoice_model = "failed"
            _vibevoice_processor = None
    return _vibevoice_model, _vibevoice_processor

def generate_voice_preset(wav_path: str, preset_out_path: str):
    """Generates a VibeVoice prefilled speaker preset from a WAV file."""
    model, processor = get_vibevoice_pipeline()
    if model == "failed" or model is None:
        raise RuntimeError("VibeVoice model not loaded")

    import librosa
    import torch
    from transformers.cache_utils import DynamicCache
    from transformers.modeling_outputs import BaseModelOutputWithPast
    
    device = next(model.parameters()).device
    dtype = next(model.parameters()).dtype
    
    import tempfile
    import subprocess
    import imageio_ffmpeg
    
    # Transcode incoming audio (m4a, webm, wav, mp3, etc.) to 24kHz mono PCM WAV for librosa
    temp_wav_fd, temp_wav_path = tempfile.mkstemp(suffix=".wav")
    os.close(temp_wav_fd)
    
    try:
        cmd = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-i", wav_path,
            "-acodec", "pcm_s16le",
            "-ar", "24000",
            "-ac", "1",
            temp_wav_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"FFmpeg audio transcoding failed: {res.stderr}")
            
        wav, sr = librosa.load(temp_wav_path, sr=24000)
    finally:
        if os.path.exists(temp_wav_path):
            try:
                os.remove(temp_wav_path)
            except Exception:
                pass

    if processor.db_normalize and processor.audio_normalizer:
        wav = processor.audio_normalizer(wav)
        
    speech_tensor = torch.tensor(wav, dtype=torch.float32).unsqueeze(0).to(device)
    
    with torch.no_grad():
        encoder_output = model.model.acoustic_tokenizer.encode(speech_tensor.unsqueeze(1))
        audio_tokens = encoder_output.sample(model.model.acoustic_tokenizer.std_dist_type)[0]
        
        if torch.isnan(model.model.speech_scaling_factor) or torch.isnan(model.model.speech_bias_factor):
            scaling_factor = 1. / audio_tokens.flatten().std()
            bias_factor = -audio_tokens.flatten().mean()
        else:
            scaling_factor = model.model.speech_scaling_factor
            bias_factor = model.model.speech_bias_factor
            
        audio_features = (audio_tokens + bias_factor) * scaling_factor
        connect_features = model.model.acoustic_connector(audio_features)
        
    latent_len = audio_features.shape[1]
    
    system_prompt = " Transform the text provided by various speakers into speech output, utilizing the distinct voice of each respective speaker.\n"
    voice_header = " Voice input:\n Speaker 0:"
    text_input_header = "\n Text input:\n"
    neg_prompt = "<|image_pad|>"
    
    sys_toks = processor.tokenizer.encode(system_prompt)
    voice_hdr_toks = processor.tokenizer.encode(voice_header, add_special_tokens=False)
    text_hdr_toks = processor.tokenizer.encode(text_input_header, add_special_tokens=False)
    neg_toks = processor.tokenizer.encode(neg_prompt, add_special_tokens=False)
    
    speech_start_id = processor.tokenizer.speech_start_id
    speech_end_id = processor.tokenizer.speech_end_id
    speech_diffusion_id = processor.tokenizer.speech_diffusion_id
    
    lm_tokens = sys_toks + voice_hdr_toks + [speech_start_id] + [speech_diffusion_id] * latent_len + [speech_end_id] + text_hdr_toks
    lm_input_ids = torch.tensor([lm_tokens], dtype=torch.long, device=device)
    lm_attn_mask = torch.ones_like(lm_input_ids)
    
    tts_text_masks = torch.ones_like(lm_input_ids)
    speech_start_idx = len(sys_toks) + len(voice_hdr_toks) + 1
    tts_text_masks[0, speech_start_idx : speech_start_idx + latent_len] = 0
    
    with torch.no_grad():
        # Cast connect features if model is bfloat16
        connect_features_cast = connect_features.to(dtype=dtype)
        
        lm_outputs = model.forward_lm(
            input_ids=lm_input_ids,
            attention_mask=lm_attn_mask,
            use_cache=True,
            return_dict=True
        )
        
        x = model.get_input_embeddings()(lm_input_ids)
        x[0, speech_start_idx : speech_start_idx + latent_len, :] = connect_features_cast[0]
        x = x + model.model.tts_input_types(tts_text_masks.long())
        
        tts_lm_outputs = model.model.tts_language_model(
            inputs_embeds=x,
            attention_mask=lm_attn_mask,
            use_cache=True,
            return_dict=True
        )
        
        neg_input_ids = torch.tensor([neg_toks], dtype=torch.long, device=device)
        neg_attn_mask = torch.ones_like(neg_input_ids)
        neg_outputs = model.forward_lm(
            input_ids=neg_input_ids,
            attention_mask=neg_attn_mask,
            use_cache=True,
            return_dict=True
        )
        
        neg_x = model.get_input_embeddings()(neg_input_ids)
        neg_x = neg_x + model.model.tts_input_types(torch.ones_like(neg_input_ids).long())
        neg_tts_outputs = model.model.tts_language_model(
            inputs_embeds=neg_x,
            attention_mask=neg_attn_mask,
            use_cache=True,
            return_dict=True
        )
        
    def to_dynamic_cache(pkv):
        if isinstance(pkv, DynamicCache):
            return pkv
        cache = DynamicCache()
        for layer_idx, (k, v) in enumerate(pkv):
            cache.update(k, v, layer_idx)
        return cache
        
    voice_preset = {
        "lm": BaseModelOutputWithPast(
            last_hidden_state=lm_outputs.last_hidden_state.cpu(),
            past_key_values=to_dynamic_cache(lm_outputs.past_key_values)
        ),
        "tts_lm": BaseModelOutputWithPast(
            last_hidden_state=tts_lm_outputs.last_hidden_state.cpu(),
            past_key_values=to_dynamic_cache(tts_lm_outputs.past_key_values)
        ),
        "neg_lm": BaseModelOutputWithPast(
            last_hidden_state=neg_outputs.last_hidden_state.cpu(),
            past_key_values=to_dynamic_cache(neg_outputs.past_key_values)
        ),
        "neg_tts_lm": BaseModelOutputWithPast(
            last_hidden_state=neg_tts_outputs.last_hidden_state.cpu(),
            past_key_values=to_dynamic_cache(neg_tts_outputs.past_key_values)
        )
    }
    
    torch.save(voice_preset, preset_out_path)
    logger.info(f"Custom voice preset saved to {preset_out_path}")

def pcm_to_wav(pcm_data: bytes, sample_rate: int = 16000) -> bytes:
    """Wraps raw mono PCM bytes in a WAV container."""
    wav_buf = io.BytesIO()
    with wave.open(wav_buf, 'wb') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2) # 16-bit PCM
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_data)
    return wav_buf.getvalue()

def normalize_text_for_tts(text: str) -> str:
    """Cleans up text for Text-to-Speech synthesis by stripping markdown, emojis, task IDs, and expanding numbers to words."""
    import re
    from num2words import num2words
    
    # 1. Remove markdown syntax like *, _, `, #, -, [ ]
    text = re.sub(r'[*_`#\-]', ' ', text)
    text = re.sub(r'\[\s*x?\s*\]', ' ', text)
    
    # 2. Filter out specific database identifiers like task_1780851954
    text = re.sub(r'task\s+\d+|task\d+|task\s*_\s*\d+', ' ', text)
    
    # 3. Filter out long numbers (e.g. 8+ digits) that shouldn't be read out loud
    text = re.sub(r'\b\d{8,}\b', ' ', text)
    
    # 4. Remove emojis or decorative symbols (e.g., checkmarks, warning icons, bells)
    text = re.sub(r'[\u2700-\u27BF]|[\uE000-\uF8FF]|\uD83C[\uDC00-\uDFFF]|\uD83D[\uDC00-\uDFFF]|[\u2011-\u26FF]|\uD83E[\uDD00-\uDFFF]', ' ', text)
    
    # 5. Clean remaining function name underscores to spaces for natural speech (e.g. get_current_schedule -> get current schedule)
    text = re.sub(r'([a-zA-Z])_([a-zA-Z])', r'\1 \2', text)
    
    # 5a. Replace timezone abbreviations with common tongue names
    tz_map = {
        r'\bPST\b': 'Pacific Time',
        r'\bPDT\b': 'Pacific Time',
        r'\bEST\b': 'Eastern Time',
        r'\bEDT\b': 'Eastern Time',
        r'\bCST\b': 'Central Time',
        r'\bCDT\b': 'Central Time',
        r'\bMST\b': 'Mountain Time',
        r'\bMDT\b': 'Mountain Time',
        r'\bUTC\b': 'Greenwich Mean Time',
        r'\bGMT\b': 'Greenwich Mean Time'
    }
    for tz_pattern, tz_expanded in tz_map.items():
        text = re.sub(tz_pattern, tz_expanded, text, flags=re.IGNORECASE)
        
    # 5b. Format times to be read conversationally (e.g. 10:00 AM -> 10 AM, 10:30 PM -> 10 thirty PM, 2:05 -> 2 oh 5)
    def replace_hour_only_time(match):
        hour = int(match.group(1))
        meridiem = match.group(2)
        if meridiem:
            return f"{hour} {meridiem}"
        else:
            return f"{hour} o'clock"
            
    text = re.sub(r'\b(\d{1,2}):00\s*(AM|PM|am|pm)?\b', replace_hour_only_time, text)
    
    def replace_standard_time(match):
        hour = int(match.group(1))
        minutes = int(match.group(2))
        meridiem = match.group(3)
        if minutes == 0:
            min_str = ""
        elif minutes < 10:
            min_str = f"oh {minutes}"
        else:
            min_str = str(minutes)
        meridiem_str = f" {meridiem}" if meridiem else ""
        return f"{hour} {min_str}{meridiem_str}"
        
    text = re.sub(r'\b(\d{1,2}):(\d{2})\s*(AM|PM|am|pm)?\b', replace_standard_time, text)

    # 6. Expand other digits/numbers to words
    def replace_number(match):
        num_str = match.group(0)
        try:
            return num2words(int(num_str))
        except Exception:
            return num_str
    text = re.sub(r'\b\d+\b', replace_number, text)
    
    # 7. Normalize extra whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def synthesize_text_to_pcm(text: str, voice: str = 'af_heart') -> bytes:
    """Synthesizes text to raw mono PCM bytes."""
    text = normalize_text_for_tts(text)
    if not text:
        return b""
        
    # Check if custom cloned voice is requested
    if voice == 'custom_cloned':
        model, processor = get_vibevoice_pipeline()
        if model != "failed" and model is not None:
            try:
                import torch
                import copy
                from transformers.cache_utils import DynamicCache
                from transformers.modeling_outputs import BaseModelOutputWithPast
                
                device = next(model.parameters()).device
                
                _data_dir = os.path.join(os.path.expanduser("~"), ".quantime")
                preset_path = os.path.join(_data_dir, "user_voice_ref.pt")
                if not os.path.exists(preset_path):
                    logger.warning(f"Custom preset {preset_path} not found. Synthesizing using default Carter preset.")
                    preset_path = os.path.join(os.path.dirname(__file__), "vibevoice_src", "demo", "voices", "streaming_model", "en-Carter_man.pt")
                
                with torch.serialization.safe_globals([BaseModelOutputWithPast, DynamicCache]):
                    voice_preset = torch.load(preset_path, map_location=device, weights_only=False)
                
                # Move to correct device and dtype
                model_dtype = next(model.parameters()).dtype
                for k in voice_preset:
                    voice_preset[k].last_hidden_state = voice_preset[k].last_hidden_state.to(device=device, dtype=model_dtype)
                    cache = voice_preset[k].past_key_values
                    if hasattr(cache, 'layers'):
                        for layer in cache.layers:
                            if hasattr(layer, 'keys') and layer.keys is not None:
                                layer.keys = layer.keys.to(device=device, dtype=model_dtype)
                            if hasattr(layer, 'values') and layer.values is not None:
                                layer.values = layer.values.to(device=device, dtype=model_dtype)
                            if hasattr(layer, 'key_cache') and layer.key_cache is not None:
                                layer.key_cache = layer.key_cache.to(device=device, dtype=model_dtype)
                            if hasattr(layer, 'value_cache') and layer.value_cache is not None:
                                layer.value_cache = layer.value_cache.to(device=device, dtype=model_dtype)
                    if hasattr(cache, '_key_cache'):
                        for layer_idx in range(len(cache._key_cache)):
                            cache._key_cache[layer_idx] = cache._key_cache[layer_idx].to(device=device, dtype=model_dtype)
                            cache._value_cache[layer_idx] = cache._value_cache[layer_idx].to(device=device, dtype=model_dtype)
                    elif hasattr(cache, 'key_cache'):
                        for layer_idx in range(len(cache.key_cache)):
                            cache.key_cache[layer_idx] = cache.key_cache[layer_idx].to(device=device, dtype=model_dtype)
                            cache.value_cache[layer_idx] = cache.value_cache[layer_idx].to(device=device, dtype=model_dtype)
                            
                inputs = processor.process_input_with_cached_prompt(
                    text=text,
                    cached_prompt=voice_preset,
                    padding=True,
                    return_tensors="pt",
                    return_attention_mask=True,
                )
                for k, v in inputs.items():
                    if torch.is_tensor(v):
                        inputs[k] = v.to(device)
                        
                outputs = model.generate(
                    **inputs,
                    max_new_tokens=None,
                    cfg_scale=1.5,
                    tokenizer=processor.tokenizer,
                    generation_config={'do_sample': False},
                    verbose=False,
                    all_prefilled_outputs=copy.deepcopy(voice_preset),
                )
                
                if outputs.speech_outputs and outputs.speech_outputs[0] is not None:
                    audio_tensor = outputs.speech_outputs[0]
                    if torch.is_tensor(audio_tensor):
                        audio_np = audio_tensor.detach().cpu().to(torch.float32).numpy()
                    else:
                        audio_np = np.asarray(audio_tensor, dtype=np.float32)
                    
                    audio_np = np.clip(audio_np, -1.0, 1.0)
                    pcm_data = (audio_np * 32767.0).astype(np.int16).tobytes()
                    return pcm_data
            except Exception as e:
                logger.error(f"VibeVoice synthesis error: {e}. Falling back to Kokoro.")
        else:
            logger.warning("VibeVoice model failed to initialize. Falling back to Kokoro.")

    # Fallback to Kokoro
    pipeline = get_tts_pipeline()
    if pipeline == "mock" or pipeline is None:
        # Generate basic sine wave mock voice if Kokoro fails to load
        logger.warning("Generating mock audio response (Kokoro inactive).")
        sample_rate = 24000
        duration = 0.5
        t = np.linspace(0, duration, int(sample_rate * duration), False)
        tone = np.sin(2 * np.pi * 440 * t)
        audio_int16 = (tone * 32767).astype(np.int16)
        return audio_int16.tobytes()

    try:
        import torch
        generator = pipeline(text, voice=voice if voice != 'custom_cloned' else 'af_heart', speed=1.0, split_pattern=r'\n+')
        audio_chunks = []
        for _, _, audio in generator:
            if audio is not None:
                if isinstance(audio, torch.Tensor):
                    audio = audio.cpu().numpy()
                audio_int16 = (audio * 32767).astype(np.int16)
                audio_chunks.append(audio_int16.tobytes())
        return b"".join(audio_chunks)
    except Exception as e:
        logger.error(f"TTS synthesis error: {e}")
        return b""

class SimpleSilenceDetector:
    """
    Computes Root Mean Square (RMS) energy to detect speech and silence boundaries.
    """
    def __init__(self, sample_rate: int = 16000, silence_threshold: float = 0.015, silence_duration_sec: float = 1.0):
        self.sample_rate = sample_rate
        self.silence_threshold = silence_threshold
        self.silence_duration_frames = int((sample_rate * silence_duration_sec) / 2) # 16-bit PCM is 2 bytes per sample
        self.silent_samples_count = 0
        self.has_spoken = False

    def is_silence(self, pcm_chunk: bytes) -> bool:
        if not pcm_chunk:
            return False
            
        samples = np.frombuffer(pcm_chunk, dtype=np.int16).astype(np.float32) / 32768.0
        if len(samples) == 0:
            return False
            
        rms = np.sqrt(np.mean(samples**2))
        
        if rms > self.silence_threshold:
            self.has_spoken = True
            self.silent_samples_count = 0
            return False
        else:
            if self.has_spoken:
                self.silent_samples_count += len(samples)
                if self.silent_samples_count >= self.silence_duration_frames:
                    self.reset()
                    return True
            return False

    def reset(self):
        self.silent_samples_count = 0
        self.has_spoken = False

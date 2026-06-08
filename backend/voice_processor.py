# backend/voice_processor.py
import os
os.environ["HF_HUB_OFFLINE"] = "1"

import io
import wave
import logging
import numpy as np
import sys

logger = logging.getLogger("quantime.voice_processor")

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
            _vibevoice_processor = VibeVoiceStreamingProcessor.from_pretrained(model_path)
            
            # Decide dtype & attention implementation
            if device == "cuda":
                load_dtype = torch.bfloat16
                attn_impl = "flash_attention_2"
            else:
                load_dtype = torch.float32
                attn_impl = "sdpa"
                
            _vibevoice_model = VibeVoiceStreamingForConditionalGenerationInference.from_pretrained(
                model_path,
                torch_dtype=load_dtype,
                device_map=device,
                attn_implementation=attn_impl
            )
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
    
    wav, sr = librosa.load(wav_path, sr=24000)
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

def synthesize_text_to_pcm(text: str, voice: str = 'af_heart') -> bytes:
    """Synthesizes text to raw mono PCM bytes."""
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
                
                # Move to correct device
                for k in voice_preset:
                    voice_preset[k].last_hidden_state = voice_preset[k].last_hidden_state.to(device)
                    cache = voice_preset[k].past_key_values
                    if hasattr(cache, '_key_cache'):
                        for layer_idx in range(len(cache._key_cache)):
                            cache._key_cache[layer_idx] = cache._key_cache[layer_idx].to(device)
                            cache._value_cache[layer_idx] = cache._value_cache[layer_idx].to(device)
                    elif hasattr(cache, 'key_cache'):
                        for layer_idx in range(len(cache.key_cache)):
                            cache.key_cache[layer_idx] = cache.key_cache[layer_idx].to(device)
                            cache.value_cache[layer_idx] = cache.value_cache[layer_idx].to(device)
                            
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

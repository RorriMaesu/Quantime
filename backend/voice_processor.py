# backend/voice_processor.py
import os
import platform
import logging

# Configure early logging so startup diagnostics in this module are visible in uvicorn
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("quantime.voice_processor")

# On Windows, when run under background environments (like Task Scheduler/VBScript/Installer),
# user environment variables (such as custom HF_HOME or OLLAMA_MODELS) may be missing or default to the Admin profile.
# We restore them directly from the registry to ensure offline caches are resolved correctly.
if platform.system() == "Windows":
    try:
        import winreg
        # List of registry keys to scan (HKCU, HKLM)
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
                            expanded_value = os.path.expandvars(str(value))
                            # Always override if the registry has a configured path
                            if os.environ.get(name) != expanded_value:
                                os.environ[name] = expanded_value
                                logger.info(f"Restored registry env: {name}={expanded_value}")
                        i += 1
            except OSError:
                pass
        
        # Scan HKEY_USERS to resolve standard user profiles when running elevated as Administrator
        try:
            with winreg.OpenKey(winreg.HKEY_USERS, "") as users_key:
                u_idx = 0
                while True:
                    sid_name = winreg.EnumKey(users_key, u_idx)
                    if not sid_name.startswith(".") and len(sid_name) > 10:
                        try:
                            with winreg.OpenKey(winreg.HKEY_USERS, rf"{sid_name}\Environment", 0, winreg.KEY_READ) as env_key:
                                e_idx = 0
                                while True:
                                    name, value, val_type = winreg.EnumValue(env_key, e_idx)
                                    if name in ("HF_HOME", "HF_HUB_CACHE", "OLLAMA_MODELS"):
                                        expanded_value = os.path.expandvars(str(value))
                                        # Always override if registry has configured path
                                        if os.environ.get(name) != expanded_value:
                                            os.environ[name] = expanded_value
                                            logger.info(f"Restored SID {sid_name} env: {name}={expanded_value}")
                                    e_idx += 1
                        except OSError:
                            pass
                    u_idx += 1
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

def get_tts_pipeline():
    global _kokoro_pipeline
    if _kokoro_pipeline is None or _kokoro_pipeline == "mock":
        try:
            from kokoro import KPipeline
            logger.info("Initializing Kokoro-82M TTS Pipeline...")
            try:
                _kokoro_pipeline = KPipeline(lang_code='a') # 'a' for American English
            except Exception as e:
                logger.info(f"Kokoro not found in local cache ({e}). Attempting online download...")
                old_offline = os.environ.get("HF_HUB_OFFLINE")
                if old_offline:
                    del os.environ["HF_HUB_OFFLINE"]
                try:
                    _kokoro_pipeline = KPipeline(lang_code='a')
                finally:
                    if old_offline:
                        os.environ["HF_HUB_OFFLINE"] = old_offline
            logger.info("Kokoro TTS Pipeline loaded successfully.")
        except Exception as e:
            logger.error(f"Failed to load Kokoro-82M TTS: {e}. Falling back to mock TTS.")
            _kokoro_pipeline = "mock"
    return _kokoro_pipeline

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
    
    # 0a. Strip schedule proposals and their inner JSON content entirely
    text = re.sub(r'<schedule-proposal[^>]*>.*?</schedule-proposal>', ' ', text, flags=re.DOTALL)
    
    # 0. Strip any XML/HTML tags (like <tool_call>...</tool_call> or <|channel>) to prevent tag leaks
    text = re.sub(r'<[^>]+>', ' ', text)
    
    # 1. Remove markdown syntax, brackets, parentheses, curly braces, and pipes
    text = re.sub(r'[*_`#\-]', ' ', text)
    text = re.sub(r'\[\s*x?\s*\]', ' ', text)
    text = re.sub(r'[\[\](){}|]', ' ', text)
    
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
    def to_12_hour(hour, meridiem):
        if meridiem:
            return hour, meridiem.upper()
        if hour >= 12:
            return (hour - 12 if hour > 12 else 12), "PM"
        else:
            return (12 if hour == 0 else hour), "AM"

    def replace_hour_only_time(match):
        hour = int(match.group(1))
        meridiem = match.group(2)
        h, m = to_12_hour(hour, meridiem)
        return f"{h} {m}"
            
    text = re.sub(r'\b(\d{1,2}):00\s*(AM|PM|am|pm)?\b', replace_hour_only_time, text)
    
    def replace_standard_time(match):
        hour = int(match.group(1))
        minutes = int(match.group(2))
        meridiem = match.group(3)
        h, m = to_12_hour(hour, meridiem)
        if minutes == 0:
            min_str = ""
        elif minutes < 10:
            min_str = f" oh {minutes}"
        else:
            min_str = f" {minutes}"
        return f"{h}{min_str} {m}"
        
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
        
    # Map custom cloned voice option to default Kokoro heart voice
    if voice == 'custom_cloned' or not voice:
        voice = 'af_heart'

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
        try:
            generator = pipeline(text, voice=voice, speed=1.0, split_pattern=r'\n+')
            audio_chunks = []
            for _, _, audio in generator:
                if audio is not None:
                    if isinstance(audio, torch.Tensor):
                        audio = audio.cpu().numpy()
                    audio_int16 = (audio * 32767).astype(np.int16)
                    audio_chunks.append(audio_int16.tobytes())
            return b"".join(audio_chunks)
        except Exception as inner_e:
            if voice != 'af_heart':
                logger.warning(f"Voice '{voice}' failed to synthesize offline ({inner_e}). Falling back to cached 'af_heart'.")
                generator = pipeline(text, voice='af_heart', speed=1.0, split_pattern=r'\n+')
                audio_chunks = []
                for _, _, audio in generator:
                    if audio is not None:
                        if isinstance(audio, torch.Tensor):
                            audio = audio.cpu().numpy()
                        audio_int16 = (audio * 32767).astype(np.int16)
                        audio_chunks.append(audio_int16.tobytes())
                return b"".join(audio_chunks)
            else:
                raise
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

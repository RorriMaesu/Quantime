# backend/voice_processor.py
import io
import wave
import logging
import numpy as np

logger = logging.getLogger("quantime.voice_processor")

# Lazy-loaded TTS Pipeline to prevent boot-up delays
_kokoro_pipeline = None

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

def pcm_to_wav(pcm_data: bytes, sample_rate: int = 16000) -> bytes:
    """Converts 16-bit integer mono PCM bytes into 32-bit float WAV normalized to [-1, 1] using soundfile."""
    import soundfile as sf
    # Convert 16-bit integer PCM to 32-bit float normalized to [-1.0, 1.0]
    samples = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32) / 32768.0
    wav_buf = io.BytesIO()
    sf.write(wav_buf, samples, sample_rate, format='WAV', subtype='FLOAT')
    return wav_buf.getvalue()

def synthesize_text_to_pcm(text: str, voice: str = 'af_heart') -> bytes:
    """Synthesizes text to raw 24kHz 16-bit mono PCM bytes using Kokoro."""
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
        generator = pipeline(text, voice=voice, speed=1.0, split_pattern=r'\n+')
        audio_chunks = []
        for _, _, audio in generator:
            if audio is not None:
                # Convert PyTorch Tensor to NumPy array if necessary
                if isinstance(audio, torch.Tensor):
                    audio = audio.cpu().numpy()
                # Convert float32 to int16
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
            
        # Convert bytes to int16 numpy array
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

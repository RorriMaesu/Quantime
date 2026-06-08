# backend/voice_stt.py
import io
import wave
import logging
import speech_recognition as sr

logger = logging.getLogger("quantime.voice_stt")

class LocalSpeechToText:
    def __init__(self):
        self.recognizer = sr.Recognizer()
        logger.info("Initialized SpeechRecognition for offline fallback.")

    def transcribe_pcm(self, pcm_data: bytes, sample_rate: int = 16000) -> str:
        """Transcribes raw mono PCM bytes using speech_recognition."""
        try:
            # Wrap bytes in a WAV container so SpeechRecognition can read it
            wav_buf = io.BytesIO()
            with wave.open(wav_buf, 'wb') as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2) # 16-bit PCM
                wav_file.setframerate(sample_rate)
                wav_file.writeframes(pcm_data)
            wav_buf.seek(0)

            with sr.AudioFile(wav_buf) as source:
                audio = self.recognizer.record(source)
            
            # Use Google Recognition API (free, built-in) as robust default client-side fallback
            text = self.recognizer.recognize_google(audio)
            return text.strip()
        except sr.UnknownValueError:
            # Silence or unrecognizable audio
            return ""
        except sr.RequestError as re:
            logger.error(f"Speech recognition request failure: {re}")
            return ""
        except Exception as e:
            logger.error(f"Error during audio transcription: {e}")
            return ""

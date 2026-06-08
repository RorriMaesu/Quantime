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
        except (sr.RequestError, Exception) as re:
            logger.info(f"Google speech recognition failed/offline ({re}). Falling back to local Gemma 4 multimodal transcription...")
            try:
                import base64
                import json
                import urllib.request
                from backend.ollama_agent import get_selected_model, OLLAMA_CHAT_URL
                
                # We already have the WAV bytes in wav_buf
                wav_buf.seek(0)
                wav_bytes = wav_buf.read()
                audio_b64 = base64.b64encode(wav_bytes).decode("utf-8")
                
                model_name = get_selected_model()
                payload = {
                    "model": model_name,
                    "messages": [
                        {
                            "role": "user",
                            "content": "Please transcribe the speech in the attached audio. Output only the transcription, no other text or explanation.",
                            "images": [audio_b64]
                        }
                    ],
                    "stream": False
                }
                
                req = urllib.request.Request(
                    OLLAMA_CHAT_URL,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                
                with urllib.request.urlopen(req, timeout=45) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                    text = res.get("message", {}).get("content", "").strip()
                    logger.info(f"Local Gemma 4 transcription response: {text}")
                    return text
            except Exception as ex:
                logger.error(f"Local Gemma 4 multimodal transcription fallback failed: {ex}")
                return ""

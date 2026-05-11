import base64
import httpx
import logging
from typing import Optional
from app.core.config import get_settings

logger = logging.getLogger(__name__)


class TTSService:
    """Text-to-speech service using ElevenLabs API."""

    def __init__(self):
        settings = get_settings()
        self._api_key = settings.ELEVENLABS_API_KEY
        self._voice_id = settings.ELEVENLABS_VOICE_ID
        self._model_id = settings.ELEVENLABS_MODEL_ID
        self._base_url = "https://api.elevenlabs.io/v1"

    def generate(self, text: str) -> Optional[str]:
        """
        Generate speech audio from text using ElevenLabs API.

        Args:
            text: The text to convert to speech

        Returns:
            Base64-encoded MP3 audio string, or None if TTS is disabled
        """
        if not self._api_key:
            logger.warning("[TTS] ElevenLabs API key not configured, skipping TTS")
            return None

        if not text or not text.strip():
            logger.warning("[TTS] Empty text provided, skipping TTS")
            return None

        try:
            url = f"{self._base_url}/text-to-speech/{self._voice_id}"
            headers = {
                "xi-api-key": self._api_key,
                "Content-Type": "application/json",
            }
            payload = {
                "text": text,
                "model_id": self._model_id,
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75,
                },
            }

            logger.info(f"[TTS] Calling ElevenLabs for text: '{text[:60]}...'")

            with httpx.Client() as client:
                response = client.post(url, json=payload, headers=headers, timeout=30.0)
                response.raise_for_status()

            audio_bytes = response.content
            audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")

            logger.info(f"[TTS] Successfully generated audio ({len(audio_bytes)} bytes)")
            return audio_base64

        except httpx.HTTPStatusError as e:
            logger.error(f"[TTS] ElevenLabs API error: {e.response.status_code} — {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"[TTS] Unexpected error: {e}", exc_info=True)
            return None


tts_service = TTSService()

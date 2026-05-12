import json
import tempfile
import os
from datetime import date

from google import genai
from google.genai import types
from fastapi import UploadFile, HTTPException

from app.core.config import get_settings
from app.core.intent_prompt import get_intent_system_prompt
from app.core.logging import logger

TRANSCRIPTION_ONLY_PROMPT = """\
Transcribe this audio EXACTLY as spoken. IMPORTANT: Write everything in Latin letters (Romanize).
For Urdu words, use Roman Urdu spelling (e.g., "mera", "blood pressure" → "blood pressure", not "بلڈ پریشر").
Output ONLY the Romanized transcription on the first line. On the second line, output the language: 'en', 'ur', or 'mixed'.
"""

COMBINED_PROMPT_PREFIX = """\
Audio from a Pakistani user. Do TWO things and output ONLY valid JSON:
1. Transcribe exactly as spoken. Romanize Urdu words using Latin letters (e.g., "mera account logout kardo" not "میرا اکاؤنٹ لاگ اؤٹ کر دو"). Keep English/medicine names/numbers in Latin script as-is.
2. Parse intent from the transcription using these rules:
"""

COMBINED_PROMPT_SUFFIX = """
RESPOND WITH ONLY THIS JSON (no markdown, no preamble, no explanation):
{{"text": "<transcription>", "language": "en|ur|mixed", "action": "<action>", "params": {{...}}}}
"""


class VoiceCommandService:
    def __init__(self):
        settings = get_settings()
        self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self._model = settings.GEMINI_MODEL

    async def process(self, file: UploadFile, language: str = None) -> dict:
        logger.info(f"Processing voice command (user language: {language})")
        suffix = os.path.splitext(file.filename or "audio.m4a")[1] or ".m4a"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name

        try:
            file_size = os.path.getsize(tmp_path)
            logger.info(f"Audio file size: {file_size} bytes ({file_size / 1024:.1f} KB)")

            uploaded_file = self._client.files.upload(file=tmp_path)

            # Step 1: Transcription only (faster, smaller prompt)
            logger.info("Calling Gemini for transcription only")
            response = self._client.models.generate_content(
                model=self._model,
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_uri(
                                file_uri=uploaded_file.uri,
                                mime_type=uploaded_file.mime_type,
                            ),
                            types.Part(text=TRANSCRIPTION_ONLY_PROMPT),
                        ],
                    )
                ],
                config=types.GenerateContentConfig(
                    max_output_tokens=200,
                ),
            )

            raw = response.text.strip()
            logger.info(f"Transcription response ({len(raw)} chars): {raw!r}")

            # Parse transcription output
            lines = raw.rsplit("\n", 1)
            if len(lines) == 2:
                text = lines[0].strip()
                language = lines[1].strip().lower()
            else:
                text = raw
                language = "unknown"

            logger.info(f"Transcribed: {text!r} (language: {language})")

            # Check for logout keyword
            if self._matches_logout(text):
                logger.info("Logout detected, returning instantly")
                return {
                    "text": text,
                    "language": language,
                    "action": "logout",
                    "params": {},
                }

            # For other commands, call intent parser
            logger.info("Other command detected, parsing intent")
            today = date.today().isoformat()
            prompt = COMBINED_PROMPT_PREFIX + get_intent_system_prompt(today) + COMBINED_PROMPT_SUFFIX

            response = self._client.models.generate_content(
                model=self._model,
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part(text=text),
                            types.Part(text=prompt),
                        ],
                    )
                ],
                config=types.GenerateContentConfig(
                    max_output_tokens=400,
                ),
            )

            raw = response.text.strip()
            logger.info(f"Intent response ({len(raw)} chars): {raw!r}")

            result = json.loads(raw)
            return {
                "text": text,
                "language": language,
                "action": result.get("action", "unknown"),
                "params": result.get("params", {}),
            }

        except json.JSONDecodeError as e:
            logger.error(f"Voice command returned non-JSON: {response.text!r}")
            logger.error(f"JSON decode error: {e}")
            return {"text": "", "language": "unknown", "action": "unknown", "params": {}}
        except Exception as e:
            # Catch API errors (quota, model unavailable, etc) and return generic message
            error_msg = str(e).lower()
            if any(x in error_msg for x in ['quota', 'rate limit', 'unavailable', 'overloaded']):
                logger.error(f"Gemini API error: {e}")
                raise HTTPException(status_code=503, detail="Model not available. Please try again later.")
            logger.error(f"Voice command error: {e}")
            raise
        finally:
            os.unlink(tmp_path)

    def _matches_logout(self, text: str) -> bool:
        """Check if text contains logout keywords."""
        logout_keywords = ["logout", "log out", "log me out", "لاگ آؤٹ", "logout karo", "لاگ آؤٹ کرو"]
        text_lower = text.lower()
        return any(keyword.lower() in text_lower for keyword in logout_keywords)


voice_command_service = VoiceCommandService()

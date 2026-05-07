import json
from datetime import date

from google import genai
from google.genai import types
from fastapi import HTTPException

from app.core.config import get_settings
from app.core.intent_prompt import get_intent_system_prompt
from app.core.logging import logger


class IntentService:
    def __init__(self):
        settings = get_settings()
        self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self._model = settings.GEMINI_MODEL

    def parse(self, text: str, language: str = "en") -> dict:
        try:
            today = date.today().isoformat()
            system_prompt = get_intent_system_prompt(today, language)

            response = self._client.models.generate_content(
                model=self._model,
                contents=[types.Content(role="user", parts=[types.Part(text=text)])],
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    max_output_tokens=800,
                ),
            )

            # Log full response structure for debugging
            logger.info(f"Gemini finish reason: {response.candidates[0].finish_reason if response.candidates else 'NO_CANDIDATES'}")
            logger.info(f"Gemini usage: {response.usage_metadata}")

            raw = response.text.strip()
            logger.info(f"Gemini raw intent response: {raw!r}")

            # Strip markdown code fences if present (```json ... ```)
            if raw.startswith("```"):
                # Remove opening fence line (e.g. ```json or ```)
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

            return json.loads(raw)

        except json.JSONDecodeError:
            logger.error(f"Gemini returned non-JSON for intent parsing: {response.text!r}")
            return {"action": "unknown", "params": {}}
        except Exception as e:
            logger.error(f"Intent parsing error: {e}")
            raise HTTPException(status_code=502, detail="Intent parsing service temporarily unavailable")


intent_service = IntentService()

import json
import re
from google import genai
from google.genai import types
from fastapi import HTTPException

from app.core.config import get_settings
from app.core.system_prompt import SYSTEM_PROMPT
from app.core.logging import logger
from app.schemas.chat import ChatRequest, ChatResponse


class ChatService:
    def __init__(self):
        settings = get_settings()
        self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self._model = settings.GEMINI_MODEL
        self._max_tokens = settings.GEMINI_MAX_TOKENS

    def chat(self, request: ChatRequest) -> ChatResponse:
        try:
            # Convert messages: Gemini uses "model" instead of "assistant"
            contents = []
            for m in request.messages:
                role = "model" if m.role == "assistant" else "user"
                contents.append(types.Content(role=role, parts=[types.Part(text=m.content)]))

            response = self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    max_output_tokens=self._max_tokens,
                ),
            )

            response_text = response.text.strip()

            # Fix common proper noun misspellings from Gemini
            response_text = self._fix_proper_nouns(response_text)

            # Check if response is JSON (language conversion case)
            try:
                parsed = json.loads(response_text)
                if "detected_language" in parsed and "converted_to_urdu" in parsed:
                    return ChatResponse(
                        content=parsed.get("response", ""),
                        model=self._model,
                        detected_language=parsed.get("detected_language"),
                        converted_to_urdu=parsed.get("converted_to_urdu"),
                    )
            except json.JSONDecodeError:
                pass

            # Normal response (English or Urdu input)
            return ChatResponse(
                content=response_text,
                model=self._model,
            )

        except Exception as e:
            error_msg = str(e).lower()
            logger.error(f"Gemini API error: {e}")
            # Catch quota/rate limit/model errors and return generic message
            if any(x in error_msg for x in ['quota', 'rate limit', 'unavailable', 'overloaded', 'model']):
                raise HTTPException(status_code=503, detail="Model not available. Please try again later.")
            raise HTTPException(status_code=502, detail="Chatbot service temporarily unavailable")

    def _fix_proper_nouns(self, text: str) -> str:
        """
        Fix common proper noun misspellings from Gemini.
        Handles Isra/IsraHealthcare variations in both English and Urdu.
        """
        # English variations: Isri, Isre, isri, isre → IsraHealthcare
        text = re.sub(r'\b[Ii]sr[ie]\b', 'Isra', text)
        text = re.sub(r'\b[Ii]sra\s*health\s*care\b', 'IsraHealthcare', text, flags=re.IGNORECASE)
        text = re.sub(r'\b[Ii]sra\s*healthcare\b', 'IsraHealthcare', text, flags=re.IGNORECASE)

        # Urdu variations: اسری، اسرے، اسري → اسرا
        # Also fix healthcare variations
        text = text.replace('اسری', 'اسرا')
        text = text.replace('اسرے', 'اسرا')
        text = text.replace('اسري', 'اسرا')

        # Fix common Urdu variations of IsraHealthcare
        text = re.sub(r'اسر[ای]\s*ہیلتھ\s*کیئر', 'اسرا ہیلتھ کیئر', text)
        text = re.sub(r'اسر[ای]\s*ہيلتھ\s*کيئر', 'اسرا ہیلتھ کیئر', text)

        return text


chat_service = ChatService()

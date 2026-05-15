import json
import re
from google import genai
from google.genai import types
from fastapi import HTTPException

from app.core.config import get_settings
from app.core.system_prompt import SYSTEM_PROMPT
from app.core.logging import logger
from app.schemas.chat import ChatRequest, ChatResponse


def _detect_text_language(text: str) -> str:
    """Detect if text is English or Urdu script."""
    if not text:
        return "en"
    # Check for Urdu/Arabic script
    urdu_pattern = r'[؀-ۿ]'
    urdu_chars = len(re.findall(urdu_pattern, text))
    # If >20% of characters are Urdu script, treat as Urdu
    return "ur" if urdu_chars > len(text) * 0.2 else "en"


class ChatService:
    def __init__(self):
        settings = get_settings()
        self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self._model = settings.GEMINI_MODEL
        self._max_tokens = settings.GEMINI_MAX_TOKENS

    def _format_health_context(self, health_context: dict) -> str:
        """Format health context into readable text for the prompt."""
        if not health_context:
            logger.info(f"[Chat] No health context provided")
            return ""

        logs = health_context.get("recentHealthLogs", [])
        meds = health_context.get("todaysMedicines", {})
        logger.info(f"[Chat] Formatting health context: {len(logs) if logs else 0} logs, medicines data present: {bool(meds)}")

        context_text = "\n[USER HEALTH CONTEXT - Use this to provide personalized advice]\n"

        # Format recent health logs
        if logs and len(logs) > 0:
            context_text += "\nRecent Health Logs (last 7 days):\n"
            by_type = {}
            for log in logs:
                if not isinstance(log, dict):
                    logger.warning(f"[Chat] Skipping non-dict log entry: {log}")
                    continue
                log_type = log.get("log_type", "unknown")
                if log_type not in by_type:
                    by_type[log_type] = []
                by_type[log_type].append(log)

            for log_type, entries in sorted(by_type.items()):
                context_text += f"  {log_type}: "
                # Format values properly (blood_pressure needs systolic/diastolic format)
                values = []
                for e in entries:
                    if log_type == "blood_pressure":
                        # Blood pressure: systolic/diastolic format
                        systolic = e.get("value_numeric", "?")
                        diastolic = e.get("value_text", "?")
                        values.append(f"{systolic}/{diastolic}")
                    else:
                        # Other readings: just the numeric or text value
                        val = e.get("value_numeric")
                        if val is None:
                            val = e.get("value_text", "N/A")
                        values.append(str(val))
                context_text += ", ".join(values[:5]) + ("\n" if len(values) <= 5 else f", ... ({len(values)} total)\n")
            logger.debug(f"[Chat] Formatted {len(logs)} health logs into context")
        else:
            logger.debug(f"[Chat] No health logs to format")

        # Format today's medicines
        medicines_data = health_context.get("todaysMedicines", {})
        medications = medicines_data.get("medications", []) or medicines_data.get("todo_list", {}).get("medications", [])
        if medications and len(medications) > 0:
            context_text += "\nToday's Medications:\n"
            for med in medications:
                if not isinstance(med, dict):
                    logger.warning(f"[Chat] Skipping non-dict medicine entry: {med}")
                    continue
                name = med.get("medication_name", "Unknown")
                slots = med.get("slots", [])
                if slots and len(slots) > 0:
                    times = [s.get("time_slot", "") for s in slots if isinstance(s, dict)]
                    dosages = [s.get("dosage", "") for s in slots if isinstance(s, dict)]
                    context_text += f"  - {name}: {', '.join(times)} ({', '.join(dosages)})\n"
            logger.debug(f"[Chat] Formatted {len(medications)} medications into context")
        else:
            logger.debug(f"[Chat] No medications to format")

        context_text += "\n[END HEALTH CONTEXT]\n"
        return context_text

    def chat(self, request: ChatRequest) -> ChatResponse:
        try:
            # Detect language of the current user message
            current_message = request.messages[-1].content if request.messages else ""
            detected_lang = _detect_text_language(current_message)
            logger.info(f"[Chat] Detected language: {detected_lang}")

            # Format health context if provided
            health_context_text = ""
            if request.healthContext:
                health_context_text = self._format_health_context(request.healthContext.model_dump())
                logger.info(f"[Chat] Health context provided: {len(health_context_text)} chars")

            # Convert messages: Gemini uses "model" instead of "assistant"
            contents = []
            for i, m in enumerate(request.messages):
                role = "model" if m.role == "assistant" else "user"
                # Add language instruction to the current user message (last one)
                content_text = m.content
                if i == len(request.messages) - 1 and m.role == "user":
                    # Add language instruction to the final user message
                    if detected_lang == "ur":
                        content_text = f"{content_text}\n\n[LANGUAGE: URDU] Respond ONLY in Urdu script (اردو). Do not use Roman Urdu or English."
                    else:
                        content_text = f"{content_text}\n\n[LANGUAGE: ENGLISH] Respond ONLY in English. Do not use Urdu or any other language."
                contents.append(types.Content(role=role, parts=[types.Part(text=content_text)]))

            # Combine system prompt with health context
            full_system_prompt = SYSTEM_PROMPT
            if health_context_text:
                full_system_prompt += health_context_text

            response = self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=full_system_prompt,
                    max_output_tokens=self._max_tokens,
                ),
            )

            response_text = response.text.strip()

            # Fix common proper noun misspellings from Gemini
            response_text = self._fix_proper_nouns(response_text)

            # Check if response is JSON (language conversion case - shouldn't happen in text chat)
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

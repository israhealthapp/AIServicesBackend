from google import genai
from google.genai import types
from fastapi import HTTPException
from datetime import datetime

from app.core.config import get_settings
from app.core.logging import logger
from app.schemas.caregiver_summary import CaregiverSummaryRequest, ChatResponse


CAREGIVER_SYSTEM_PROMPT = """You are an AI health assistant specialized in providing insights for caregivers.

Your role:
- Analyze patient health data and identify important trends
- Highlight medication adherence patterns and concerns
- Flag vital sign abnormalities (high/low blood pressure, blood sugar)
- Provide actionable recommendations for caregivers
- Always recommend consulting healthcare professionals for medical decisions
- Be concise and focus on what the caregiver needs to know

Analysis framework:
- Medication adherence: Is the patient taking meds consistently?
- Vital trends: Are there improving or worsening patterns?
- Mood/mental health: Any signs of distress or positive outlook?
- Urgent concerns: Any red flags that need immediate attention?

Guidelines:
- NEVER diagnose or prescribe medications
- For emergencies, advise contacting Rescue 1122 or going to nearest ER
- Respect patient privacy
- Be empathetic and professional
- Provide data-backed insights when possible
"""


class CaregiverSummaryService:
    def __init__(self):
        settings = get_settings()
        self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self._model = settings.GEMINI_MODEL
        self._max_tokens = settings.GEMINI_MAX_TOKENS

    def generate_summary(self, request: CaregiverSummaryRequest) -> ChatResponse:
        try:
            context_text = self._format_health_context(request.context)
            full_prompt = f"""{context_text}

Caregiver Question/Request:
{request.message}"""

            logger.info(
                f"[Caregiver Summary] Generating summary for health_id={request.context.health_id}, "
                f"language={request.language}, message_len={len(request.message)}"
            )

            response = self._client.models.generate_content(
                model=self._model,
                contents=[types.Content(role="user", parts=[types.Part(text=full_prompt)])],
                config=types.GenerateContentConfig(
                    system_instruction=CAREGIVER_SYSTEM_PROMPT,
                    max_output_tokens=self._max_tokens,
                ),
            )

            logger.info(f"[Caregiver Summary] Response ready ({len(response.text)} chars)")

            return ChatResponse(
                content=response.text,
                model=self._model,
                role="assistant",
            )

        except Exception as e:
            logger.error(f"Caregiver summary generation error: {e}", exc_info=True)
            raise HTTPException(status_code=502, detail="Summary generation service temporarily unavailable")

    def _format_health_context(self, context) -> str:
        """Format health context into a readable string for Gemini."""
        lines = [
            f"Patient: {context.first_name}",
            f"Health ID: {context.health_id}",
        ]

        if context.age:
            lines.append(f"Age: {context.age}")
        if context.blood_type:
            lines.append(f"Blood Type: {context.blood_type}")

        if context.conditions:
            lines.append(f"Known Conditions: {', '.join(context.conditions)}")

        # Medication Adherence Summary
        med_summary = context.medication_summary
        if med_summary.total_slots > 0:
            adherence_pct = (med_summary.taken / med_summary.total_slots) * 100
            lines.append(f"\nMedication Adherence (Today):")
            lines.append(f"  - Total Slots: {med_summary.total_slots}")
            lines.append(f"  - Taken: {med_summary.taken}")
            lines.append(f"  - Pending: {med_summary.pending}")
            lines.append(f"  - Missed: {med_summary.missed}")
            lines.append(f"  - Adherence Rate: {adherence_pct:.1f}%")

        # Recent Medications
        if context.medication_timeline:
            lines.append(f"\nRecent Medication Activity (Last 7 days):")
            for med in context.medication_timeline[-10:]:  # Show last 10
                status_emoji = "✓" if med.status == "taken" else "✗"
                lines.append(
                    f"  {status_emoji} {med.medication_name} {med.dosage or ''} "
                    f"@ {med.time_slot or 'unknown'}"
                )

        # Health Logs
        if context.latest_health_logs:
            lines.append(f"\nRecent Health Logs (Last 7 days):")
            for log in context.latest_health_logs[-15:]:  # Show last 15
                value = log.value_numeric or log.value_text or "—"
                logged_date = self._parse_date(log.logged_at)
                lines.append(f"  [{logged_date}] {log.log_type}: {value}")

        return "\n".join(lines)

    @staticmethod
    def _parse_date(iso_string: str | None) -> str:
        """Parse ISO datetime and return readable format."""
        if not iso_string:
            return "unknown date"
        try:
            dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return "unknown date"


caregiver_summary_service = CaregiverSummaryService()

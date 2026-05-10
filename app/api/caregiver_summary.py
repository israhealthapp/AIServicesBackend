from fastapi import APIRouter, Depends

from app.schemas.caregiver_summary import CaregiverSummaryRequest, ChatResponse
from app.services.caregiver_summary_service import caregiver_summary_service
from app.core.auth import verify_token
from app.db import history as db

router = APIRouter(prefix="/api", tags=["caregiver"])


@router.post("/caregiver-summary", response_model=ChatResponse)
def generate_caregiver_summary(request: CaregiverSummaryRequest, user=Depends(verify_token)):
    """Generate an AI health summary for a caregiver based on patient health data."""
    response = caregiver_summary_service.generate_summary(request)

    if user:
        med = request.context.medication_summary
        total_logged = med.taken + med.skipped + med.missed
        adherence_pct = (med.taken / total_logged * 100) if total_logged > 0 else None

        db.save_caregiver_summary(
            caregiver_user_id=user.id,
            health_user_id=request.context.health_id,
            request_message=request.message,
            summary_content=response.content,
            language=request.language,
            model_name=response.model,
            health_context_snapshot=request.context.model_dump(),
            patient_conditions=request.context.conditions or None,
            medication_adherence_pct=adherence_pct,
        )

    return response

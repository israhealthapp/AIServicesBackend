from fastapi import APIRouter

from app.schemas.caregiver_summary import CaregiverSummaryRequest, ChatResponse
from app.services.caregiver_summary_service import caregiver_summary_service

router = APIRouter(prefix="/api", tags=["caregiver"])


@router.post("/caregiver-summary", response_model=ChatResponse)
def generate_caregiver_summary(request: CaregiverSummaryRequest):
    """Generate an AI health summary for a caregiver based on patient health data."""
    return caregiver_summary_service.generate_summary(request)

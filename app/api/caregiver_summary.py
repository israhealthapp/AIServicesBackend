from fastapi import APIRouter, Depends

from app.schemas.caregiver_summary import CaregiverSummaryRequest, ChatResponse
from app.services.caregiver_summary_service import caregiver_summary_service
from app.core.auth import verify_token

router = APIRouter(prefix="/api", tags=["caregiver"])


@router.post("/caregiver-summary", response_model=ChatResponse)
def generate_caregiver_summary(request: CaregiverSummaryRequest, user = Depends(verify_token)):
    """Generate an AI health summary for a caregiver based on patient health data."""
    return caregiver_summary_service.generate_summary(request)

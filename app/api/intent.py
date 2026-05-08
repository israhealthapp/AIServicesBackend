from fastapi import APIRouter, Depends

from app.schemas.intent import IntentRequest, IntentResponse
from app.services.intent_service import intent_service
from app.core.auth import verify_token

router = APIRouter(prefix="/api", tags=["intent"])


@router.post("/parse_intent", response_model=IntentResponse)
def parse_intent(request: IntentRequest, user = Depends(verify_token)):
    """Parse a voice command into a structured intent."""
    return intent_service.parse(request.text, request.language)

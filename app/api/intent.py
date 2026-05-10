import json

from fastapi import APIRouter, Depends

from app.schemas.intent import IntentRequest, IntentResponse
from app.services.intent_service import intent_service
from app.core.auth import verify_token
from app.db import history as db

router = APIRouter(prefix="/api", tags=["intent"])


@router.post("/parse_intent", response_model=IntentResponse)
def parse_intent(request: IntentRequest, user=Depends(verify_token)):
    """Parse a voice command into a structured intent."""
    result = intent_service.parse(request.text, request.language)

    if user:
        session_id = db.create_session(
            user.id, "voice_command",
            language=request.language,
            title=request.text[:80],
        )
        if session_id:
            db.save_message(session_id, user.id, "user", request.text, sequence_number=1)
            assistant_msg_id = db.save_message(
                session_id, user.id, "assistant",
                json.dumps({"action": result.get("action"), "params": result.get("params", {})}),
                sequence_number=2,
            )
            if assistant_msg_id:
                db.save_intent_result(
                    assistant_msg_id, user.id,
                    action=result.get("action", "unknown"),
                    params=result.get("params", {}),
                    source="text",
                )
            db.close_session(session_id)

    return result

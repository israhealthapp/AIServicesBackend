from fastapi import APIRouter, Depends

from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import chat_service
from app.core.auth import verify_token
from app.db import history as db

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, user=Depends(verify_token)):
    """Send a message to the IsraHealthcare chatbot and get a response."""
    response = chat_service.chat(request)

    if user:
        title = request.messages[-1].content[:80]
        session_id = db.create_session(user.id, "text_chat", title=title)
        if session_id:
            for i, msg in enumerate(request.messages, start=1):
                db.save_message(session_id, user.id, msg.role, msg.content, sequence_number=i)
            db.save_message(
                session_id,
                user.id,
                "assistant",
                response.content,
                sequence_number=len(request.messages) + 1,
                model_name=response.model,
                detected_language=response.detected_language,
                converted_to_urdu=response.converted_to_urdu,
            )

    return response


@router.get("/health")
def health():
    return {"status": "ok"}

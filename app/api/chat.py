from fastapi import APIRouter, Depends

from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import chat_service
from app.core.auth import verify_token

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, user = Depends(verify_token)):
    """Send a message to the IsraHealthcare chatbot and get a response."""
    return chat_service.chat(request)


@router.get("/health")
def health():
    return {"status": "ok"}

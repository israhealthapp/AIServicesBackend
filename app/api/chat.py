from fastapi import APIRouter

from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import chat_service

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    """Send a message to the IsraHealthcare chatbot and get a response."""
    return chat_service.chat(request)


@router.get("/health")
def health():
    return {"status": "ok"}

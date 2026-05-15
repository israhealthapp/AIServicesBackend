from pydantic import BaseModel, Field
from typing import Optional


class Message(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., min_length=1, max_length=10000)


class HealthContext(BaseModel):
    recentHealthLogs: list = Field(default_factory=list)
    todaysMedicines: dict = Field(default_factory=dict)


class ChatRequest(BaseModel):
    messages: list[Message] = Field(..., min_length=1, max_length=100)
    healthContext: Optional[HealthContext] = None


class ChatResponse(BaseModel):
    role: str = "assistant"
    content: str
    model: str
    detected_language: Optional[str] = None
    converted_to_urdu: Optional[str] = None

from pydantic import BaseModel, Field
from typing import Any


class IntentRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)
    language: str = Field(default="en", pattern="^(en|ur)$")


class IntentResponse(BaseModel):
    action: str
    params: dict[str, Any]

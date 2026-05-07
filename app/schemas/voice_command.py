from pydantic import BaseModel
from typing import Any


class VoiceCommandResponse(BaseModel):
    text: str
    language: str
    action: str
    params: dict[str, Any]

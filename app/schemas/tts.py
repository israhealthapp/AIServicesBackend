from pydantic import BaseModel, Field


class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)


class TTSResponse(BaseModel):
    audio_base64: str

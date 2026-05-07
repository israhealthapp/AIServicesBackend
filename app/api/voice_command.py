from fastapi import APIRouter, UploadFile, File, Form, HTTPException

from app.schemas.voice_command import VoiceCommandResponse
from app.services.voice_command_service import voice_command_service

router = APIRouter(prefix="/api", tags=["voice_command"])


@router.post("/voice_command", response_model=VoiceCommandResponse)
async def voice_command(
    file: UploadFile = File(...),
    language: str = Form(None)
):
    """Transcribe audio and parse intent in a single Gemini call."""
    if not file.content_type or not file.content_type.startswith("audio/"):
        raise HTTPException(status_code=400, detail="File must be an audio file")

    result = await voice_command_service.process(file, language)

    if not result["text"]:
        raise HTTPException(status_code=422, detail="No speech detected in audio")

    return result

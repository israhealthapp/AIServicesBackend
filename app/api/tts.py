from fastapi import APIRouter, Depends, HTTPException
from app.schemas.tts import TTSRequest, TTSResponse
from app.services.tts_service import tts_service
from app.core.auth import verify_token

router = APIRouter(prefix="/api", tags=["tts"])


@router.post("/tts", response_model=TTSResponse)
def generate_tts(request: TTSRequest, user=Depends(verify_token)):
    """
    Generate speech audio from text using ElevenLabs.
    Returns base64-encoded MP3 audio.
    """
    audio_base64 = tts_service.generate(request.text)
    if audio_base64 is None:
        raise HTTPException(
            status_code=503,
            detail="TTS service not available (API key not configured)",
        )
    return TTSResponse(audio_base64=audio_base64)

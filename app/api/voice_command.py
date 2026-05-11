import json
import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.schemas.voice_command import VoiceCommandResponse
from app.services.voice_command_service import voice_command_service
from app.core.auth import verify_token
from app.db import history as db

router = APIRouter(prefix="/api", tags=["voice_command"])


@router.post("/voice_command", response_model=VoiceCommandResponse)
async def voice_command(
    file: UploadFile = File(...),
    language: str = Form(None),
    user=Depends(verify_token),
):
    """Transcribe audio and parse intent in a single Gemini call."""
    if not file.content_type or not file.content_type.startswith("audio/"):
        raise HTTPException(status_code=400, detail="File must be an audio file")

    file_size = None
    try:
        # Read size before handing off (service reads the file internally)
        pos = file.file.tell()
        file.file.seek(0, 2)
        file_size = file.file.tell()
        file.file.seek(pos)
    except Exception:
        pass

    result = await voice_command_service.process(file, language)

    if not result["text"]:
        raise HTTPException(status_code=422, detail="No speech detected in audio")

    if user:
        detected_lang = result.get("language")
        session_id = db.create_session(
            user.id, "voice_command",
            language=detected_lang,
            title=result["text"][:80],
        )
        if session_id:
            user_msg_id = db.save_message(
                session_id, user.id, "user", result["text"], sequence_number=1
            )
            if user_msg_id:
                db.save_voice_metadata(
                    user_msg_id,
                    transcription_provider="gemini",
                    audio_file_size_bytes=file_size,
                    detected_language=detected_lang,
                )
            assistant_msg_id = db.save_message(
                session_id, user.id, "assistant",
                json.dumps({"action": result["action"], "params": result["params"]}),
                sequence_number=2,
            )
            if assistant_msg_id:
                db.save_intent_result(
                    assistant_msg_id, user.id,
                    action=result["action"],
                    params=result["params"] or {},
                    source="voice_upload",
                )
            db.close_session(session_id)

    return result

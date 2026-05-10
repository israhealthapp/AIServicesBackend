"""
Supabase persistence layer for AI interaction history.

All write functions are fire-and-forget: they log errors but never raise,
so a DB failure never disrupts the main response flow.
"""
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

from supabase import create_client, Client

from app.core.config import get_settings
from app.core.logging import logger


@lru_cache()
def _get_db_client() -> Client:
    settings = get_settings()
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Write helpers ─────────────────────────────────────────────────────────────

def create_session(
    user_id: str,
    session_type: str,
    language: Optional[str] = None,
    title: Optional[str] = None,
) -> Optional[str]:
    try:
        session_id = str(uuid.uuid4())
        ts = _now()
        _get_db_client().table("ai_sessions").insert({
            "id": session_id,
            "user_id": user_id,
            "session_type": session_type,
            "language": language,
            "title": title,
            "created_at": ts,
            "updated_at": ts,
        }).execute()
        return session_id
    except Exception as exc:
        logger.error(f"[DB] create_session failed: {exc}")
        return None


def close_session(session_id: str) -> None:
    try:
        ts = _now()
        _get_db_client().table("ai_sessions").update({
            "ended_at": ts,
            "updated_at": ts,
        }).eq("id", session_id).execute()
    except Exception as exc:
        logger.error(f"[DB] close_session failed: {exc}")


def save_message(
    session_id: str,
    user_id: str,
    role: str,
    content: str,
    sequence_number: int = 1,
    model_name: Optional[str] = None,
    detected_language: Optional[str] = None,
    converted_to_urdu: Optional[str] = None,
) -> Optional[str]:
    try:
        message_id = str(uuid.uuid4())
        ts = _now()
        row: dict = {
            "id": message_id,
            "session_id": session_id,
            "user_id": user_id,
            "role": role,
            "content": content,
            "sequence_number": sequence_number,
            "created_at": ts,
            "updated_at": ts,
        }
        if model_name:
            row["model_name"] = model_name
        if detected_language:
            row["detected_language"] = detected_language
        if converted_to_urdu:
            row["converted_to_urdu"] = converted_to_urdu
        _get_db_client().table("ai_messages").insert(row).execute()
        return message_id
    except Exception as exc:
        logger.error(f"[DB] save_message failed: {exc}")
        return None


def save_intent_result(
    message_id: str,
    user_id: str,
    action: str,
    params: dict,
    source: str = "text",
) -> None:
    try:
        ts = _now()
        _get_db_client().table("ai_intent_results").insert({
            "id": str(uuid.uuid4()),
            "message_id": message_id,
            "user_id": user_id,
            "action": action,
            "params": params,
            "source": source,
            "created_at": ts,
            "updated_at": ts,
        }).execute()
    except Exception as exc:
        logger.error(f"[DB] save_intent_result failed: {exc}")


def save_voice_metadata(
    message_id: str,
    transcription_provider: str,
    transcription_confidence: Optional[float] = None,
    audio_duration_seconds: Optional[float] = None,
    audio_file_size_bytes: Optional[int] = None,
    detected_language: Optional[str] = None,
    raw_transcript: Optional[str] = None,
) -> None:
    try:
        ts = _now()
        row: dict = {
            "id": str(uuid.uuid4()),
            "message_id": message_id,
            "transcription_provider": transcription_provider,
            "created_at": ts,
            "updated_at": ts,
        }
        if transcription_confidence is not None:
            row["transcription_confidence"] = transcription_confidence
        if audio_duration_seconds is not None:
            row["audio_duration_seconds"] = audio_duration_seconds
        if audio_file_size_bytes is not None:
            row["audio_file_size_bytes"] = audio_file_size_bytes
        if detected_language is not None:
            row["detected_language"] = detected_language
        if raw_transcript is not None:
            row["raw_transcript"] = raw_transcript
        _get_db_client().table("ai_voice_metadata").insert(row).execute()
    except Exception as exc:
        logger.error(f"[DB] save_voice_metadata failed: {exc}")


def save_caregiver_summary(
    caregiver_user_id: str,
    health_user_id: str,
    request_message: str,
    summary_content: str,
    language: str = "en",
    model_name: Optional[str] = None,
    health_context_snapshot: Optional[dict] = None,
    patient_conditions: Optional[list] = None,
    medication_adherence_pct: Optional[float] = None,
) -> None:
    try:
        ts = _now()
        row: dict = {
            "id": str(uuid.uuid4()),
            "caregiver_user_id": caregiver_user_id,
            "health_user_id": health_user_id,
            "request_message": request_message,
            "summary_content": summary_content,
            "language": language,
            "created_at": ts,
            "updated_at": ts,
        }
        if model_name:
            row["model_name"] = model_name
        if health_context_snapshot is not None:
            row["health_context_snapshot"] = health_context_snapshot
        if patient_conditions:
            row["patient_conditions"] = patient_conditions
        if medication_adherence_pct is not None:
            row["medication_adherence_pct"] = medication_adherence_pct
        _get_db_client().table("ai_caregiver_summaries").insert(row).execute()
    except Exception as exc:
        logger.error(f"[DB] save_caregiver_summary failed: {exc}")


# ── Read helpers ──────────────────────────────────────────────────────────────

def get_sessions(
    user_id: str, page: int = 1, page_size: int = 20
) -> tuple[list, int]:
    try:
        offset = (page - 1) * page_size
        result = (
            _get_db_client()
            .table("ai_sessions")
            .select("*", count="exact")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .range(offset, offset + page_size - 1)
            .execute()
        )
        return result.data or [], result.count or 0
    except Exception as exc:
        logger.error(f"[DB] get_sessions failed: {exc}")
        return [], 0


def get_session_with_messages(session_id: str, user_id: str) -> Optional[dict]:
    try:
        client = _get_db_client()
        session = (
            client.table("ai_sessions")
            .select("*")
            .eq("id", session_id)
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        if not session.data:
            return None
        messages = (
            client.table("ai_messages")
            .select("*")
            .eq("session_id", session_id)
            .order("sequence_number")
            .execute()
        )
        return {**session.data, "messages": messages.data or []}
    except Exception as exc:
        logger.error(f"[DB] get_session_with_messages failed: {exc}")
        return None


def get_intent_history(
    user_id: str, page: int = 1, page_size: int = 20
) -> tuple[list, int]:
    try:
        offset = (page - 1) * page_size
        result = (
            _get_db_client()
            .table("ai_intent_results")
            .select("*, ai_messages(content)", count="exact")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .range(offset, offset + page_size - 1)
            .execute()
        )
        rows = []
        for r in result.data or []:
            msg = r.pop("ai_messages", None) or {}
            rows.append({**r, "message_content": msg.get("content")})
        return rows, result.count or 0
    except Exception as exc:
        logger.error(f"[DB] get_intent_history failed: {exc}")
        return [], 0


def get_caregiver_summaries(
    user_id: str, page: int = 1, page_size: int = 20
) -> tuple[list, int]:
    try:
        offset = (page - 1) * page_size
        result = (
            _get_db_client()
            .table("ai_caregiver_summaries")
            .select("*", count="exact")
            .eq("caregiver_user_id", user_id)
            .order("created_at", desc=True)
            .range(offset, offset + page_size - 1)
            .execute()
        )
        return result.data or [], result.count or 0
    except Exception as exc:
        logger.error(f"[DB] get_caregiver_summaries failed: {exc}")
        return [], 0


def get_all_history(user_id: str) -> dict:
    """
    Return all sessions (with messages embedded) and caregiver summaries for a user.
    Uses 3 queries total to avoid N+1 per session.
    """
    client = _get_db_client()
    try:
        sessions_res = (
            client.table("ai_sessions")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as exc:
        logger.error(f"[DB] get_all_history sessions query failed: {exc}")
        sessions_res = type("R", (), {"data": []})()

    try:
        messages_res = (
            client.table("ai_messages")
            .select("*")
            .eq("user_id", user_id)
            .order("sequence_number")
            .execute()
        )
    except Exception as exc:
        logger.error(f"[DB] get_all_history messages query failed: {exc}")
        messages_res = type("R", (), {"data": []})()

    try:
        summaries_res = (
            client.table("ai_caregiver_summaries")
            .select("*")
            .eq("caregiver_user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as exc:
        logger.error(f"[DB] get_all_history summaries query failed: {exc}")
        summaries_res = type("R", (), {"data": []})()

    # Group messages by session_id
    messages_by_session: dict[str, list] = {}
    for msg in messages_res.data or []:
        messages_by_session.setdefault(msg["session_id"], []).append(msg)

    sessions = [
        {**s, "messages": messages_by_session.get(s["id"], [])}
        for s in (sessions_res.data or [])
    ]

    return {
        "sessions": sessions,
        "caregiver_summaries": summaries_res.data or [],
    }

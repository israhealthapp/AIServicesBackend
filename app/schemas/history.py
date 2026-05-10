from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class MessageItem(BaseModel):
    id: str
    role: str
    content: str
    sequence_number: int
    model_name: Optional[str] = None
    detected_language: Optional[str] = None
    converted_to_urdu: Optional[str] = None
    created_at: datetime


class SessionListItem(BaseModel):
    id: str
    session_type: str
    language: Optional[str] = None
    title: Optional[str] = None
    created_at: datetime
    ended_at: Optional[datetime] = None


class SessionDetail(BaseModel):
    id: str
    session_type: str
    language: Optional[str] = None
    title: Optional[str] = None
    created_at: datetime
    ended_at: Optional[datetime] = None
    messages: list[MessageItem]


class IntentHistoryItem(BaseModel):
    id: str
    action: str
    params: dict[str, Any]
    source: str
    created_at: datetime
    message_content: Optional[str] = None


class CaregiverSummaryItem(BaseModel):
    id: str
    health_user_id: str
    request_message: str
    summary_content: str
    language: str
    model_name: Optional[str] = None
    patient_conditions: Optional[list[str]] = None
    medication_adherence_pct: Optional[float] = None
    created_at: datetime


class PaginatedSessions(BaseModel):
    data: list[SessionListItem]
    total: int
    page: int
    page_size: int


class PaginatedIntents(BaseModel):
    data: list[IntentHistoryItem]
    total: int
    page: int
    page_size: int


class PaginatedCaregiverSummaries(BaseModel):
    data: list[CaregiverSummaryItem]
    total: int
    page: int
    page_size: int


class FullHistory(BaseModel):
    sessions: list[SessionDetail]
    caregiver_summaries: list[CaregiverSummaryItem]

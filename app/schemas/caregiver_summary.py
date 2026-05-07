from pydantic import BaseModel, Field
from typing import Any


class HealthLog(BaseModel):
    log_type: str
    value_numeric: float | None = None
    value_text: str | None = None
    logged_at: str | None = None


class MedicationTimelineItem(BaseModel):
    medication_id: str | None = None
    medication_name: str
    slot_id: str | None = None
    time_slot: str | None = None
    dosage: str | None = None
    status: str | None = None
    taken_at: str | None = None


class MedicationSummary(BaseModel):
    date: str | None = None
    total_medications: int = 0
    total_slots: int = 0
    taken: int = 0
    pending: int = 0
    skipped: int = 0
    missed: int = 0


class HealthContext(BaseModel):
    health_id: str
    first_name: str
    latest_health_logs: list[HealthLog] = []
    medication_summary: MedicationSummary
    medication_timeline: list[MedicationTimelineItem] = []
    conditions: list[str] = []
    age: int | None = None
    blood_type: str | None = None


class CaregiverSummaryRequest(BaseModel):
    context: HealthContext
    message: str = Field(default="Generate a summary of the health status", min_length=1, max_length=5000)
    language: str = Field(default="en", pattern="^(en|ur)$")


class ChatResponse(BaseModel):
    role: str = "assistant"
    content: str
    model: str

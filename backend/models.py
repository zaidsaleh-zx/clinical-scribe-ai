from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Literal

# Upper bound on transcript size accepted by the API. A student-project safeguard:
# without this, a very large paste can make the rule-based regex passes slow and can
# silently blow past an LLM provider's context window with a confusing downstream error
# instead of a clear 422 at the door.
MAX_TRANSCRIPT_CHARS = 20_000
MAX_FIELD_CHARS = 200


class TranscriptRequest(BaseModel):
    transcript: str = Field(..., min_length=1, max_length=MAX_TRANSCRIPT_CHARS)
    use_llm: bool = False


class SoapNoteResponse(BaseModel):
    subjective: List[str]
    objective: List[str]
    assessment: List[str]
    plan: List[str]
    unclassified: List[str]
    sources: Dict[str, List[int]] = Field(default_factory=dict)
    engine: str
    completeness: Dict[str, int]
    issues: List[str]


class PatientInfo(BaseModel):
    name: Optional[str] = Field(default=None, max_length=MAX_FIELD_CHARS)
    age: Optional[str] = Field(default=None, max_length=10)
    gender: Optional[str] = Field(default=None, max_length=MAX_FIELD_CHARS)
    chief_complaint: Optional[str] = Field(default=None, max_length=MAX_FIELD_CHARS)


class SaveSessionRequest(BaseModel):
    transcript: str = Field(..., max_length=MAX_TRANSCRIPT_CHARS)
    note: dict
    patient: PatientInfo = PatientInfo()


class ExportPdfRequest(BaseModel):
    note: dict
    patient: PatientInfo = PatientInfo()


class SessionState(BaseModel):
    session_id: str
    transcript_lines: List[str] = []
    speaker_hint: Optional[str] = "patient"  # toggled by frontend as consult alternates


class SubmitSessionRequest(BaseModel):
    consultation_id: str
    note: dict
    confirm_order_routing: bool = False


class LoginRequest(BaseModel):
    api_key: str = Field(..., min_length=1, max_length=512)


class OrderPreviewRequest(BaseModel):
    note: dict


class PharmacyOrderStatusRequest(BaseModel):
    status: Literal["pending", "dispensed", "cancelled"]


class LaboratoryOrderStatusRequest(BaseModel):
    status: Literal["completed", "cancelled"]
    result: Optional[str] = Field(default=None, max_length=4000)

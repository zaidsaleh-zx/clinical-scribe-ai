"""
Clinical Documentation Assistant — backend

Two ways to use this:

1. TEXT MODE (works right now, no setup beyond `pip install fastapi uvicorn`):
   POST a transcript to /api/generate-note and get back a structured SOAP note.
   This is the fastest way to test/demo the actual "intelligence" of the app.

2. LIVE AUDIO MODE (needs faster-whisper + a working mic in the browser):
   Connect to /ws/session — stream audio chunks, get back live transcript +
   periodically-regenerated SOAP note over the same WebSocket.

Run with:
    uvicorn backend.main:app --reload --port 8000   (from the project root)
Then open http://localhost:8000 — the backend serves the frontend directly.
"""

import os
import logging
import asyncio
import hmac
import re
import string
import time
import urllib.request
from collections import defaultdict, deque
from datetime import timedelta
from typing import Optional
from dotenv import load_dotenv
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BACKEND_DIR, ".env"))

import json
import uuid
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response

from .models import (
    TranscriptRequest, SoapNoteResponse, SaveSessionRequest, ExportPdfRequest,
    SubmitSessionRequest, PharmacyOrderStatusRequest, LaboratoryOrderStatusRequest,
    LoginRequest, OrderPreviewRequest,
)
from .auth import (
    SESSION_COOKIE_NAME, SESSION_TTL_SECONDS, create_session_token,
    is_valid_session_token,
)
from .soap_generator import generate_soap
from . import db
from .pdf_export import generate_pdf
from .order_routing import preview_orders

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("clinical-scribe")

# A short bank of clinical terms fed into Whisper's prompt to nudge it toward
# correct spellings of medical vocabulary it might otherwise mis-hear
# (e.g. "blood pressure" as "blood press sure", drug names, dosage units).
CLINICAL_VOCAB_HINT = (
    "Vocabulary may include: blood pressure, heart rate, temperature, "
    "milligrams, prescribe, diagnosis, symptoms, follow-up, referral, "
    "allergies, medication, dosage."
)



_livekit_check: dict = {"ts": 0.0, "ok": None}


def _livekit_reachable(max_age: float = 60.0) -> bool:
    """Verify the LIVEKIT_* credentials actually authenticate with LiveKit Cloud.

    Checking that the env vars are present (like `livekit_configured`) isn't enough —
    a revoked or mistyped key still "looks configured" while every connection fails.
    We call the RoomService API once per `max_age` seconds and cache the result so
    `/api/status` and `/api/livekit-token` tell the truth without stalling on every
    poll with a network round-trip.
    """
    now = time.time()
    if _livekit_check["ok"] is not None and now - _livekit_check["ts"] < max_age:
        return _livekit_check["ok"]

    ok = False
    url = os.environ.get("LIVEKIT_URL")
    api_key = os.environ.get("LIVEKIT_API_KEY")
    api_secret = os.environ.get("LIVEKIT_API_SECRET")
    http_host = url.replace("wss://", "https://").replace("ws://", "http://") if url else ""
    if http_host and api_key and api_secret:
        try:
            from livekit import api

            token = (
                api.AccessToken(api_key, api_secret)
                .with_identity("scribe-status-check")
                .with_grants(api.VideoGrants(room_join=True, room="scribe-status-check"))
                .to_jwt()
            )
            request = urllib.request.Request(
                http_host + "/twirp/livekit.RoomService/ListRooms",
                data=b"{}",
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                ok = response.status == 200
        except Exception:
            logger.info("LiveKit reachability check failed (using direct-audio transport)", exc_info=True)
            ok = False

    _livekit_check.update(ts=now, ok=ok)
    return ok


def _normalize_for_dedup(text: str) -> str:
    """Collapse a sentence to a comparison key: lowercase, punctuation stripped,
    whitespace collapsed. Used so 're-transcribed' near-duplicates (which can
    differ by trailing punctuation or capitalization) are recognized as the
    same sentence instead of being added as a new line each time."""
    text = text.lower().strip()
    text = text.translate(str.maketrans("", "", string.punctuation))
    return re.sub(r"\s+", " ", text).strip()

app = FastAPI(title="Clinical Documentation Assistant")

FRONTEND_DIR = os.path.realpath(os.path.join(BACKEND_DIR, "..", "frontend"))
SAMPLE_DATA_DIR = os.path.realpath(os.path.join(BACKEND_DIR, "..", "sample_data"))

# A tiny, self-contained security layer: when a real API key is configured, protect
# the writeable or patient-sensitive routes while leaving the public health/status
# checks and local demo flows usable without friction.
API_KEY_NAMES = ("CLINICAL_SCRIBE_API_KEY", "API_KEY")
PUBLIC_PATHS = {
    "/api/health",
    "/api/status",
    "/api/sample-transcript",
    "/api/auth/status",
    "/api/auth/login",
}
REQUEST_BUCKETS = defaultdict(deque)


def _get_configured_api_key() -> Optional[str]:
    for name in API_KEY_NAMES:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return None


def _require_api_key() -> bool:
    return _get_configured_api_key() is not None


def _is_authenticated(request: Request) -> bool:
    expected = _get_configured_api_key()
    if expected is None:
        return True

    provided = request.headers.get("x-api-key")
    authorization = request.headers.get("authorization", "")
    if provided is None and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    if provided is not None and hmac.compare_digest(
        provided.encode("utf-8"),
        expected.encode("utf-8"),
    ):
        return True
    return is_valid_session_token(expected, request.cookies.get(SESSION_COOKIE_NAME))


def _is_trusted_origin(origin: str, request: Request) -> bool:
    if origin == "null":
        return origin in CORS_ORIGINS
    allowed = set(CORS_ORIGINS)
    host = request.headers.get("host", "")
    allowed.update({f"http://{host}", f"https://{host}"})
    return origin.rstrip("/") in {value.rstrip("/") for value in allowed}


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS":
        return await call_next(request)

    if path.startswith("/api/") and path not in PUBLIC_PATHS and _require_api_key():
        if not _is_authenticated(request):
            return Response(
                status_code=401,
                content=json.dumps({"error": "Authentication required."}),
                media_type="application/json",
            )

        if (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and request.cookies.get(SESSION_COOKIE_NAME)
            and not request.headers.get("x-api-key")
            and not request.headers.get("authorization")
        ):
            origin = request.headers.get("origin")
            if not origin or not _is_trusted_origin(origin, request):
                return Response(
                    status_code=403,
                    content=json.dumps({"error": "Untrusted request origin."}),
                    media_type="application/json",
                )

    if path == "/api/auth/login":
        client_ip = request.client.host if request.client else "anonymous"
        now = time.monotonic()
        bucket = REQUEST_BUCKETS[f"login:{client_ip}"]
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        if len(bucket) >= 5:
            return Response(
                status_code=429,
                content=json.dumps({"error": "Too many login attempts. Please wait a minute."}),
                media_type="application/json",
            )
        bucket.append(now)

    if path.startswith("/api/generate-note") or path.startswith("/api/export-pdf"):
        client_ip = request.client.host if request.client else "anonymous"
        now = time.monotonic()
        bucket = REQUEST_BUCKETS[client_ip]
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        if len(bucket) >= 12:
            return Response(
                status_code=429,
                content=json.dumps({"error": "Too many requests. Please slow down."}),
                media_type="application/json",
            )
        bucket.append(now)

    return await call_next(request)


def validate_runtime_config() -> None:
    """Warn on obvious misconfiguration so the app doesn't silently fall back to a dead mode."""
    warnings = []
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key and not anthropic_key.startswith("sk-ant-"):
        warnings.append("ANTHROPIC_API_KEY does not look like a valid Anthropic key (expected sk-ant-*)")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    if openrouter_key and not openrouter_key.startswith("sk-or-"):
        warnings.append("OPENROUTER_API_KEY does not look like a valid OpenRouter key (expected sk-or-*)")
    api_key = _get_configured_api_key()
    if api_key and len(api_key) < 16:
        warnings.append("CLINICAL_SCRIBE_API_KEY is set but looks unusually short")
    if os.environ.get("APP_ENV", "").lower() == "production" and not api_key:
        raise RuntimeError("CLINICAL_SCRIBE_API_KEY is required when APP_ENV=production")
    if os.environ.get("APP_ENV", "").lower() == "production" and api_key and len(api_key) < 16:
        raise RuntimeError("CLINICAL_SCRIBE_API_KEY must contain at least 16 characters in production")
    for message in warnings:
        logger.warning(message)


# CORS allows the local app and explicitly configured browser origins. Serve the
# frontend from this backend when possible instead of allowing opaque file origins.
# Built AFTER load_dotenv so a CORS_ORIGINS entry in backend/.env is honored.
_configured_cors_origins = [
    o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()
]
if "*" in _configured_cors_origins:
    logger.warning("Ignoring wildcard CORS_ORIGINS; configure explicit browser origins instead")
_configured_cors_origins = [origin for origin in _configured_cors_origins if origin != "*"]

CORS_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
] + _configured_cors_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup_validation():
    validate_runtime_config()


@app.get("/api/auth/status")
def auth_status(request: Request):
    return {
        "auth_required": _require_api_key(),
        "authenticated": _is_authenticated(request),
    }


@app.post("/api/auth/login")
def auth_login(req: LoginRequest, request: Request):
    expected = _get_configured_api_key()
    if expected is None:
        return Response(status_code=503, content='{"error":"Authentication is not configured."}', media_type="application/json")
    if not hmac.compare_digest(req.api_key.strip().encode("utf-8"), expected.encode("utf-8")):
        return Response(status_code=401, content='{"error":"Invalid access key."}', media_type="application/json")

    response = Response(content='{"authenticated":true}', media_type="application/json")
    response.set_cookie(
        SESSION_COOKIE_NAME,
        create_session_token(expected),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=(
            os.environ.get("SESSION_COOKIE_SECURE", "").lower() == "true"
            or request.url.scheme == "https"
        ),
        samesite="strict",
        path="/",
    )
    return response


@app.post("/api/auth/logout")
def auth_logout():
    response = Response(content='{"authenticated":false}', media_type="application/json")
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", httponly=True, samesite="strict")
    return response


# ---------------------------------------------------------------------------
# TEXT MODE — the core loop, no audio needed
# ---------------------------------------------------------------------------

@app.post("/api/generate-note", response_model=SoapNoteResponse)
def generate_note(req: TranscriptRequest):
    note = generate_soap(req.transcript, use_llm=req.use_llm)
    return note


@app.get("/api/sample-transcript")
def sample_transcript():
    path = os.path.join(SAMPLE_DATA_DIR, "sample_consultation.txt")
    with open(path) as f:
        return {"transcript": f.read()}


# ---------------------------------------------------------------------------
# SYSTEM STATUS — so the UI can honestly show which engines are actually live
# ---------------------------------------------------------------------------

@app.get("/api/status")
def status():
    whisper_available = False
    try:
        import faster_whisper  # noqa: F401
        whisper_available = True
    except ImportError:
        pass

    anthropic_configured = bool(os.environ.get("ANTHROPIC_API_KEY"))
    openrouter_configured = bool(os.environ.get("OPENROUTER_API_KEY"))
    livekit_configured = all(os.environ.get(name) for name in (
        "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"
    ))

    return {
        "backend": "ready",
        "whisper_installed": whisper_available,
        "llm_configured": anthropic_configured or openrouter_configured,
        "llm_provider": "anthropic" if anthropic_configured else ("openrouter" if openrouter_configured else None),
        "livekit_configured": livekit_configured,
        # Presence of env vars is NOT the same as working credentials — verify once a
        # minute so the UI chip tells the truth (the old key returned 401).
        "livekit_verified": _livekit_reachable(),
    }


@app.get("/api/livekit-token")
def livekit_token():
    """Issue a short-lived browser token without exposing the LiveKit secret."""
    livekit_url = os.environ.get("LIVEKIT_URL")
    api_key = os.environ.get("LIVEKIT_API_KEY")
    api_secret = os.environ.get("LIVEKIT_API_SECRET")
    if not all((livekit_url, api_key, api_secret)):
        return Response(
            status_code=503,
            content=json.dumps({"error": "LiveKit is not configured"}),
            media_type="application/json",
        )
    if not _livekit_reachable():
        return Response(
            status_code=503,
            content=json.dumps({
                "error": "LiveKit credentials are invalid or revoked (authentication failed). "
                "Audio still streams directly to the local Whisper model without LiveKit."
            }),
            media_type="application/json",
        )
    assert livekit_url and api_key and api_secret

    from livekit import api

    room = f"clinical-scribe-{uuid.uuid4().hex[:12]}"
    identity = f"clinician-{uuid.uuid4().hex[:8]}"
    token = (
        api.AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_name("Clinical Scribe clinician")
        .with_ttl(timedelta(hours=1))
        .with_grants(api.VideoGrants(
            room_join=True,
            room=room,
            can_publish=True,
            can_subscribe=True,
        ))
        .to_jwt()
    )
    return {"url": livekit_url, "room": room, "token": token}


@app.get("/api/health")
def health():
    """Lightweight liveness/readiness check — useful for uptime monitors, container
    orchestrators, or just confirming the backend is up before a demo."""
    return {"status": "ok"}


@app.post("/api/export-json")
def export_json(req: ExportPdfRequest):
    """Download a versioned, machine-readable SOAP representation (not a FHIR claim)."""
    export = {
        "schema": "clinicalscribe.soap-note.v1",
        "patient": req.patient.model_dump(),
        "note": req.note,
    }
    return Response(
        content=json.dumps(export, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="clinicalscribe-note.json"'},
    )


# ---------------------------------------------------------------------------
# SESSION HISTORY — lightweight SQLite-backed "recent consultations"
# ---------------------------------------------------------------------------

@app.post("/api/sessions")
def save_session(req: SaveSessionRequest):
    session_id = db.save_session(
        patient_name=req.patient.name,
        patient_age=req.patient.age,
        patient_gender=req.patient.gender,
        chief_complaint=req.patient.chief_complaint,
        transcript=req.transcript,
        note=req.note,
    )
    return {"session_id": session_id}


@app.get("/api/sessions")
def list_sessions():
    return db.list_sessions()


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    session = db.get_session(session_id)
    if not session:
        return Response(status_code=404, content=json.dumps({"error": "not found"}), media_type="application/json")
    return session


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):
    if not db.delete_session(session_id):
        return Response(status_code=404, content='{"error":"not found"}', media_type="application/json")
    return {"deleted": True, "session_id": session_id}


# ---------------------------------------------------------------------------
# SUBMIT SOAP NOTE — order creation is opt-in after clinician review
# ---------------------------------------------------------------------------
@app.post("/api/orders/preview")
def order_preview(req: OrderPreviewRequest):
    return preview_orders(req.note)


@app.post("/api/sessions/submit")
def submit_session(req: SubmitSessionRequest):
    """Save a SOAP note; route suggested orders only after explicit confirmation."""
    try:
        result = db.submit_soap_note(
            consultation_id=req.consultation_id,
            note=req.note,
            engine=req.note.get("engine", "rule_based"),
            auto_route=req.confirm_order_routing,
        )
        return result
    except db.OrdersAlreadyRoutedError as e:
        return Response(status_code=409, content=json.dumps({"error": str(e)}), media_type="application/json")
    except ValueError as e:
        return Response(status_code=404, content=json.dumps({"error": str(e)}), media_type="application/json")


# ---------------------------------------------------------------------------
# PHARMACY ORDERS
# ---------------------------------------------------------------------------
@app.get("/api/pharmacy/orders")
def list_pharmacy_orders(consultation_id: Optional[str] = None, status: Optional[str] = None):
    return db.list_pharmacy_orders(consultation_id=consultation_id, status=status)


@app.post("/api/pharmacy/orders/{order_id}/status")
def update_pharmacy_order_status(order_id: str, req: PharmacyOrderStatusRequest):
    try:
        ok = db.update_pharmacy_order_status(order_id, req.status)
    except db.InvalidPharmacyOrderTransitionError as e:
        return Response(status_code=409, content=json.dumps({"error": str(e)}), media_type="application/json")
    if not ok:
        return Response(status_code=404, content=json.dumps({"error": "not found"}), media_type="application/json")
    return {"order_id": order_id, "status": req.status}


# ---------------------------------------------------------------------------
# LABORATORY ORDERS
# ---------------------------------------------------------------------------
@app.get("/api/lab/orders")
def list_laboratory_orders(consultation_id: Optional[str] = None, status: Optional[str] = None):
    return db.list_laboratory_orders(consultation_id=consultation_id, status=status)


@app.post("/api/lab/orders/{order_id}/status")
def update_laboratory_order_status(order_id: str, req: LaboratoryOrderStatusRequest):
    if req.status == "completed" and not (req.result and req.result.strip()):
        return Response(
            status_code=422,
            content=json.dumps({"error": "A result is required to complete a laboratory order."}),
            media_type="application/json",
        )
    try:
        ok = db.update_laboratory_order_status(order_id, req.status, req.result)
    except db.InvalidLaboratoryOrderTransitionError as e:
        return Response(status_code=409, content=json.dumps({"error": str(e)}), media_type="application/json")
    if not ok:
        return Response(status_code=404, content=json.dumps({"error": "not found"}), media_type="application/json")
    return {"order_id": order_id, "status": req.status, "result": req.result}


# ---------------------------------------------------------------------------
# PDF EXPORT
# ---------------------------------------------------------------------------

@app.post("/api/export-pdf")
def export_pdf(req: ExportPdfRequest):
    pdf_bytes = generate_pdf(req.note, req.patient.model_dump())
    filename = f"soap_note_{req.patient.name or 'patient'}.pdf".replace(" ", "_")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# LIVE AUDIO MODE — WebSocket streaming
# ---------------------------------------------------------------------------

class LiveSession:
    def __init__(self):
        self.session_id = str(uuid.uuid4())
        self.transcript_lines = []
        self.last_speaker: str | None = None
        # Accumulate raw audio across chunks so Whisper can transcribe a
        # more coherent window instead of isolated micro-fragments (which is
        # what produced the garbled, "totally wrong" live transcript).
        self.audio_buffer = bytearray()  # raw PCM only — no WAV headers in here
        # Separate, never trimmed: diarization needs the full conversation.
        self.full_audio_buffer = bytearray()
        # Byte offset (into audio_buffer) of the first PCM sample that has not
        # yet been fed to Whisper. Every pass transcribes only NEW audio (plus
        # a short context tail retained before this pointer), so already-
        # transcribed words are never decoded again — this stops the per-chunk
        # cost, and the speech–to-text delay, from growing during a session.
        self.transcribed_pcm_len = 0
        self.sample_rate: int | None = None
        self.sample_width: int = 2
        self.channels: int = 1
        # Normalized (lowercase, punctuation-stripped) sentence keys already
        # emitted to the transcript, so we don't re-add the same sentence every
        # time we re-transcribe the rolling buffer. Kept separate from
        # transcript_lines because those are prefixed with "Speaker: ".
        self.emitted_keys: set[str] = set()
        # Trailing text after the last recognized sentence terminator — not yet
        # emitted, since it may still change as more audio arrives. Flushed on
        # 'finalize' (Stop Recording) so the last utterance isn't lost.
        self.pending_tail: str = ""
        # Guards against a transcription backlog: if a chunk arrives while a
        # previous transcription pass is still running, we skip *starting* a
        # new one (we still buffer the audio) rather than queuing it up. On a
        # slower CPU, queuing every audio chunk behind a slow transcription call
        # is exactly what causes the delay between speaking and seeing text
        # to keep growing over the course of a recording.
        self.transcribing: bool = False
        # Timestamp of the last SOAP-note refresh. The note is CPU-heavy, so in
        # live mode it is regenerated only when the transcript grows (rate-
        # limited to 1s) or on a slow 5s keepalive during silence.
        self.last_note_ts: float = 0.0
        # Set when a new transcript line is emitted; consumed by the throttled
        # note refresher so the SOAP panel updates live without a pass on every
        # chunk.
        self.transcript_changed: bool = False

    @property
    def min_pass_bytes(self):
        # ~1s of NEW audio before a Whisper pass is worthwhile — with the 1s
        # frontend flush cadence this means a new pass starts on almost every
        # chunk, so words land on screen ~1s after they are spoken. Below a
        # full second it's both slow and noticeably less accurate.
        return int(1.0 * (self.sample_rate or 16000) * self.sample_width * self.channels)

    @property
    def context_bytes(self):
        # ~2s of already-transcribed PCM kept before the pointer as cross-
        # boundary context for Whisper (kept sample-rate agnostic).
        return int(2.0 * (self.sample_rate or 16000) * self.sample_width * self.channels)

    @property
    def un_transcribed_pcm_len(self):
        return max(0, len(self.audio_buffer) - self.transcribed_pcm_len)

    @property
    def full_transcript(self):
        return "\n".join(self.transcript_lines)


def _split_sentences(text: str):
    """Split Whisper output into complete sentences plus a possibly-incomplete tail.

    "Complete" means ending in a sentence terminator (. ! ?). Whatever trails the last
    terminator is held back as a pending tail because it may still change once more
    audio arrives — emitting it early is what caused near-duplicate, slightly-different
    lines to pile up in the transcript.
    """
    complete_sentences = [s.strip() for s in re.findall(r"[^.!?]+[.!?]+", text) if s.strip()]
    last_terminator_end = 0
    for m in re.finditer(r"[.!?]+", text):
        last_terminator_end = m.end()
    tail = text[last_terminator_end:].strip()

    # Whisper doesn't always add terminal punctuation, especially for short/casual
    # utterances — if we held every unpunctuated fragment back forever, the transcript
    # (and the pipeline status indicator) would look permanently "stuck" even though
    # transcription is working fine. With the 1s streaming cadence, a fragment of just a few words is already visible as the live “…listening” tail, so committing at 4+ words keeps the transcript filling in word by word instead of waiting on punctuation that may never come.
    # it's very unlikely to still be "in progress", so treat it as complete instead of
    # waiting on punctuation that may never come.
    if tail and len(tail.split()) >= 4:
        complete_sentences.append(tail)
        tail = ""
    return complete_sentences, tail




async def _append_or_extend_transcript(session, websocket, sentence: str):
    """Append `sentence` to the transcript, or EDIT the last line in place when
    this sentence is a strict extension of it.

    Whisper's streaming re-decodes already-committed words each time more audio
    arrives, frequently yielding a LONGER version of the same fragment
    ('patient has a headache' -> 'patient has a headache and a fever').
    Naively appending both produced the classic live-transcription complaint of
    the same words showing up AGAIN AND AGAIN with slight additions. With this
    guard the second, fuller decode REPLACES the last line instead of being
    added as a duplicate (the client swaps the text via 'replace': True).
    """
    from .soap_generator import _infer_speaker

    key = _normalize_for_dedup(sentence)
    if not key or key in session.emitted_keys:
        return

    prev_key = ""
    last_index = len(session.transcript_lines) - 1
    if last_index >= 0:
        prev_text = session.transcript_lines[last_index].split(":", 1)[-1].strip()
        prev_key = _normalize_for_dedup(prev_text)
    if prev_key and prev_key != key:
        if key.startswith(prev_key):
            # New sentence is a fuller version of the last line -> extend it.
            session.emitted_keys.discard(prev_key)
            session.emitted_keys.add(key)
            inferred_speaker = _infer_speaker(sentence)
            if inferred_speaker:
                session.last_speaker = inferred_speaker
            speaker = (session.last_speaker or "patient").capitalize()
            line = speaker + ": " + sentence
            session.transcript_lines[last_index] = line
            session.transcript_changed = True
            await websocket.send_json({
                "type": "transcript_update",
                "line": line,
                "line_index": last_index,
                "replace": True,
                "full_transcript": session.full_transcript,
            })
            return
        if prev_key.startswith(key):
            # New decode is a SHORTER prefix of what we already committed - the
            # fuller version on screen is still the best one, so drop this one.
            return

    session.emitted_keys.add(key)
    inferred_speaker = _infer_speaker(sentence)
    if inferred_speaker:
        session.last_speaker = inferred_speaker
    # Preserve the previous role for fragments such as "and it gets worse"
    # instead of randomly changing speakers.
    speaker = (session.last_speaker or "patient").capitalize()
    line = speaker + ": " + sentence
    session.transcript_lines.append(line)
    session.transcript_changed = True
    await websocket.send_json({
        "type": "transcript_update",
        "line": line,
        "line_index": len(session.transcript_lines) - 1,
        "replace": False,
        "full_transcript": session.full_transcript,
    })


async def _transcribe_and_emit(session, websocket, wav_blob) -> str:
    """Run one Whisper pass over `wav_blob` and push per-sentence transcript lines
    plus a refreshed SOAP note to the client.

    Shared by the direct-WebSocket audio path and the (optional) LiveKit bridge path
    so both transports produce identical, duplicate-free output — sentence splitting,
    dedup and speaker inference all live here rather than in each transport separately.
    """
    from .transcription import transcribe_audio_bytes

    await websocket.send_json({"type": "transcribing"})
    recent_context = " ".join(session.transcript_lines[-3:])
    text, detected_language = await asyncio.to_thread(
        transcribe_audio_bytes,
        wav_blob,
        prompt=f"{CLINICAL_VOCAB_HINT} Clinical consultation. {recent_context}"[-1000:],
    )
    async def _refresh_note():
        """Regenerate and push the SOAP note, throttled and OFF the loop.

        generate_soap is CPU-heavy (hundreds of ms on a long transcript) and
        must never run synchronously inside the async loop — it blocks
        incoming audio chunks and grows the speech–to-text delay. It runs in
        a thread, and only when the transcript changed (rate-limited to 1s)
        or on a slow 5s keepalive during silence so the pipeline status
        indicator doesn't look stuck.
        """
        now = time.time()
        if session.transcript_changed:
            if now - session.last_note_ts < 1.0:
                return
        elif now - session.last_note_ts < 5.0:
            return
        note = await asyncio.to_thread(generate_soap, session.full_transcript, False)
        session.last_note_ts = now
        session.transcript_changed = False
        await websocket.send_json({"type": "note_update", "note": note})

    if not text:
        # No speech in this window — keep any in-progress tail intact and just
        # refresh the note so the pipeline indicator doesn't sit on "Transcribe"
        # forever during silent stretches.
        await _refresh_note()
        await websocket.send_json({"type": "partial_transcript", "text": session.pending_tail})
        return ""

    from .soap_generator import _infer_speaker

    complete_sentences, tail = _split_sentences(text)
    session.pending_tail = tail

    # Emit every completed sentence via the shared overlap-guard emitter so a
    # fuller re-decode of the previous line extends it in place instead of
    # duplicating the same words again and again.
    for sentence in complete_sentences:
        await _append_or_extend_transcript(session, websocket, sentence)

    # Always refresh the note after any non-empty transcription pass — even if this
    # particular window didn't yield a brand-new sentence, so the pipeline indicator
    # doesn't look stuck on "Capture" during long unpunctuated stretches.
    # Live "…still listening" tail: show whatever fragment Whisper currently
    # hears but is holding back (it may still change as more audio arrives),
    # so the feed never looks stale between completed sentences.
    await websocket.send_json({"type": "partial_transcript", "text": session.pending_tail})

    # Refresh the note only when the transcript grew (or on the 5s silence
    # keepalive), always off the event loop.
    await _refresh_note()
    return text


@app.websocket("/ws/session")
async def live_session(websocket: WebSocket):
    expected = _get_configured_api_key()
    if expected:
        token = websocket.cookies.get(SESSION_COOKIE_NAME)
        if not is_valid_session_token(expected, token):
            await websocket.close(code=1008, reason="Authentication required")
            return
        origin = websocket.headers.get("origin")
        allowed_origins = set(CORS_ORIGINS)
        host = websocket.headers.get("host", "")
        allowed_origins.update({f"http://{host}", f"https://{host}"})
        if not origin or origin.rstrip("/") not in {value.rstrip("/") for value in allowed_origins}:
            await websocket.close(code=1008, reason="Untrusted request origin")
            return
    await websocket.accept()
    session = LiveSession()
    livekit_bridge = None
    await websocket.send_json({"type": "session_started", "session_id": session.session_id})

    async def handle_livekit_audio(audio_bytes: bytes):
        """Adapt LiveKit's WAV chunks to the same sentence-splitting Whisper
        pipeline used by direct streaming, so both transports produce the same
        duplicate-free per-sentence transcript."""
        try:
            from .transcription import (
                is_silent_wav,
                extract_pcm_from_wav,
                wav_bytes_from_pcm,
            )

            if is_silent_wav(audio_bytes):
                return
            pcm, sample_rate, sample_width, channels = extract_pcm_from_wav(audio_bytes)
            session.sample_rate = sample_rate
            session.sample_width = sample_width
            session.channels = channels
            session.full_audio_buffer.extend(pcm)
            session.audio_buffer.extend(pcm)
            max_bytes = 10 * sample_rate * sample_width * channels
            if len(session.audio_buffer) > max_bytes:
                del session.audio_buffer[: len(session.audio_buffer) - max_bytes]

            if session.transcribing:
                return

            session.transcribing = True
            try:
                wav_blob = wav_bytes_from_pcm(
                    bytes(session.audio_buffer), sample_rate, sample_width, channels
                )
                await _transcribe_and_emit(session, websocket, wav_blob)
            finally:
                session.transcribing = False
        except WebSocketDisconnect:
            raise
        except Exception as error:
            logger.exception("LiveKit audio transcription failed")
            try:
                await websocket.send_json({"type": "error", "message": str(error)})
            except WebSocketDisconnect:
                pass

    try:
        while True:
            message = await websocket.receive()

            if message.get("type") == "websocket.disconnect":
                break

            if "bytes" in message and message["bytes"] is not None:
                # Audio chunk arrived — accumulate it and transcribe a longer window
                try:
                    from .transcription import (
                        is_silent_wav,
                        extract_pcm_from_wav,
                        wav_bytes_from_pcm,
                    )
                    audio_bytes = message["bytes"]

                    if is_silent_wav(audio_bytes):
                        continue

                    # Each incoming chunk is a *complete* WAV file (header + PCM),
                    # produced fresh by the frontend every AUDIO_CHUNK_MS. We must
                    # strip that header before appending — buffering whole WAV
                    # files back to
                    # back produces an invalid blob (stray RIFF headers embedded
                    # mid-stream), which is what caused ffmpeg/PyAV to fail with
                    # "Invalid data found when processing input: '<none>'".
                    try:
                        pcm, sample_rate, sample_width, channels = extract_pcm_from_wav(audio_bytes)
                    except Exception:
                        logger.warning("Skipping unparseable audio chunk")
                        continue
                    session.sample_rate = sample_rate
                    session.sample_width = sample_width
                    session.channels = channels
                    session.full_audio_buffer.extend(pcm)
                    # Transcribe ONLY the delta since the previous pass, keeping a bounded
                    # ~10s PCM buffer (everything that falls off the front was transcribed
                    # long ago, so the pointer is simply reset to the new buffer start).
                    session.audio_buffer.extend(pcm)

                    # Keep the buffer bounded (~10s of PCM). Everything that
                    # falls off the front was transcribed long ago, so reset the
                    # pointer to the new buffer start.
                    max_bytes = 10 * sample_rate * sample_width * channels
                    if len(session.audio_buffer) > max_bytes:
                        del session.audio_buffer[: len(session.audio_buffer) - max_bytes]
                        session.transcribed_pcm_len = len(session.audio_buffer)

                    # Don't start a Whisper pass for less than ~1.5s of NEW
                    # audio — it is slow and inaccurate. The audio stays
                    # buffered and is covered by a later pass (or by 'finalize'
                    # at the end of the recording).
                    if session.un_transcribed_pcm_len < session.min_pass_bytes:
                        continue

                    # If a transcription pass from a previous chunk is still
                    # running, don't start another one on top of it — that's
                    # what causes calls to pile up and the on-screen delay to
                    # keep growing over the course of a recording. The audio
                    # we just buffered above will simply be included in the
                    # next pass once the current one finishes.
                    if session.transcribing:
                        continue
                    session.transcribing = True
                    try:
                        # Process passes until all newly-buffered audio has
                        # been transcribed (catch-up). Audio that arrives
                        # WHILE Whisper is busy stays in the buffer and is
                        # covered by a later iteration of this loop — never
                        # advance the pointer past audio we haven't decoded.
                        while True:
                            if session.un_transcribed_pcm_len < session.min_pass_bytes:
                                break
                            # How far THIS pass will cover = buffer length now.
                            pass_end = len(session.audio_buffer)
                            window_start = max(
                                0, session.transcribed_pcm_len - session.context_bytes
                            )
                            wav_blob = wav_bytes_from_pcm(
                                bytes(session.audio_buffer[window_start:pass_end]),
                                sample_rate,
                                sample_width,
                                channels,
                            )
                            # Shared pass: sentence split + dedup + speaker
                            # inference + SOAP refresh, identical to what the
                            # LiveKit path uses.
                            await _transcribe_and_emit(session, websocket, wav_blob)
                            # Advance the pointer only up to where this pass
                            # actually started, so audio buffered during the
                            # pass is still picked up by the next iteration.
                            session.transcribed_pcm_len = pass_end
                    finally:
                        # Always release the guard, even on an early 'continue'
                        # above or an exception below otherwise one failed
                        # pass would permanently freeze future updates.

                        session.transcribing = False

                except WebSocketDisconnect:
                    raise
                except ImportError:
                    session.transcribing = False
                    await websocket.send_json({
                        "type": "error",
                        "message": (
                            "faster-whisper isn't installed. Run: pip install faster-whisper. "
                            "Note: the first transcription will download the model (~150MB) "
                            "and needs internet access."
                        ),
                    })
                except Exception as e:
                    session.transcribing = False
                    logger.exception("Live audio transcription failed")
                    try:
                        await websocket.send_json({"type": "error", "message": str(e)})
                    except WebSocketDisconnect:
                        raise


            elif "text" in message and message["text"] is not None:
                # Control messages from the frontend (e.g. manual text entry, reset)
                try:
                    payload = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue

                if payload.get("type") == "manual_line":
                    line = payload.get("line", "").strip()
                    if line:
                        session.transcript_lines.append(line)
                        note = await asyncio.to_thread(generate_soap, session.full_transcript, False)
                        await websocket.send_json({
                            "type": "transcript_update",
                            "line": line,
                            "line_index": len(session.transcript_lines) - 1,
                            "full_transcript": session.full_transcript,
                        })
                        await websocket.send_json({"type": "note_update", "note": note})

                elif payload.get("type") == "livekit_start":
                    from .livekit_bridge import LiveKitAudioBridge

                    livekit_url = os.environ.get("LIVEKIT_URL")
                    api_key = os.environ.get("LIVEKIT_API_KEY")
                    api_secret = os.environ.get("LIVEKIT_API_SECRET")
                    room = payload.get("room", "")
                    if not all((livekit_url, api_key, api_secret, room)):
                        await websocket.send_json({
                            "type": "error",
                            "message": "LiveKit is not configured on the backend.",
                        })
                        continue
                    assert livekit_url and api_key and api_secret

                    from livekit import api
                    bridge_token = (
                        api.AccessToken(api_key, api_secret)
                        .with_identity(f"scribe-backend-{session.session_id[:8]}")
                        .with_ttl(timedelta(hours=1))
                        .with_grants(api.VideoGrants(
                            room_join=True,
                            room=room,
                            can_publish=False,
                            can_subscribe=True,
                        ))
                        .to_jwt()
                    )
                    livekit_bridge = LiveKitAudioBridge(
                        livekit_url,
                        bridge_token,
                        handle_livekit_audio,
                    )
                    await livekit_bridge.start()
                    await websocket.send_json({"type": "livekit_ready"})

                elif payload.get("type") == "livekit_stop":
                    if livekit_bridge:
                        await livekit_bridge.stop()
                        livekit_bridge = None

                elif payload.get("type") == "finalize":
                    # One last transcription pass over any audio that arrived
                    # after the final chunk flush but was too short for a live
                    # pass (min_pass_bytes) — otherwise the last thing said
                    # before Stop Recording can be silently dropped.
                    if (
                        not session.transcribing
                        and session.un_transcribed_pcm_len > 0
                    ):
                        from .transcription import wav_bytes_from_pcm

                        window_start = max(
                            0, session.transcribed_pcm_len - session.context_bytes
                        )
                        wav_blob = wav_bytes_from_pcm(
                            bytes(session.audio_buffer[window_start:]),
                            session.sample_rate or 16000,
                            session.sample_width,
                            session.channels,
                        )
                        session.transcribing = True
                        try:
                            await _transcribe_and_emit(session, websocket, wav_blob)
                            session.transcribed_pcm_len = len(session.audio_buffer)
                        finally:
                            session.transcribing = False

                    # Flush any trailing sentence fragment that was held back
                    # because it hadn't been terminated by punctuation yet —
                    # otherwise the final tail can be dropped from the transcript.
                    tail = session.pending_tail.strip()
                    if tail:
                        # Same overlap-guard emitter as live passes, so the final
                        # fragment extends the previous line rather than duplicating.
                        await _append_or_extend_transcript(session, websocket, tail)
                        session.pending_tail = ""

                    # The recording is over; clear the live "…listening" line.
                    await websocket.send_json({"type": "partial_transcript", "text": ""})

                    # Keep live updates fast; use the configured LLM once at the end
                    # to produce a more coherent final SOAP draft.
                    note = await asyncio.to_thread(
                        generate_soap,
                        session.full_transcript,
                        True,
                    )
                    await websocket.send_json({"type": "note_update", "note": note})

                elif payload.get("type") == "reset":
                    session.transcript_lines = []
                    session.audio_buffer = bytearray()
                    session.full_audio_buffer = bytearray()
                    session.transcribed_pcm_len = 0
                    session.sample_rate = None
                    session.emitted_keys = set()
                    session.pending_tail = ""
                    session.transcribing = False
                    session.last_note_ts = 0.0
                    session.transcript_changed = False
                    await websocket.send_json({"type": "reset_ack"})
                    await websocket.send_json({"type": "partial_transcript", "text": ""})

    except WebSocketDisconnect:
        pass
    finally:
        if livekit_bridge:
            await livekit_bridge.stop()


# ---------------------------------------------------------------------------
# Serve the frontend
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


@app.get("/{filename}")
def frontend_files(filename: str):
    """Serve a single top-level frontend file (e.g. app.js, style.css) by name.

    SECURITY: filename comes straight from the URL, so it must be resolved and then
    checked to still be inside FRONTEND_DIR before being served — otherwise a path like
    `/../backend/main.py` (or any `..`-containing segment) could walk outside the
    frontend directory and leak source files or other host files readable by this
    process. This was a real path-traversal bug in the original version.
    """
    # Reject anything that isn't a plain filename up front.
    if "/" in filename or "\\" in filename or filename in ("..", "."):
        return Response(status_code=404, content=json.dumps({"error": "not found"}), media_type="application/json")

    candidate = os.path.realpath(os.path.join(FRONTEND_DIR, filename))
    if not candidate.startswith(FRONTEND_DIR + os.sep):
        return Response(status_code=404, content=json.dumps({"error": "not found"}), media_type="application/json")

    if os.path.isfile(candidate):
        return FileResponse(candidate)
    return Response(status_code=404, content=json.dumps({"error": "not found"}), media_type="application/json")

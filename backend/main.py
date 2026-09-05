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
import re
import string
from datetime import timedelta
from dotenv import load_dotenv
load_dotenv()  # loads variables from a local .env file, if one exists (see .env.example)

import json
import uuid
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response

from .models import TranscriptRequest, SoapNoteResponse, SaveSessionRequest, ExportPdfRequest
from .soap_generator import generate_soap
from . import db
from .pdf_export import generate_pdf

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


def _normalize_for_dedup(text: str) -> str:
    """Collapse a sentence to a comparison key: lowercase, punctuation stripped,
    whitespace collapsed. Used so 're-transcribed' near-duplicates (which can
    differ by trailing punctuation or capitalization) are recognized as the
    same sentence instead of being added as a new line each time."""
    text = text.lower().strip()
    text = text.translate(str.maketrans("", "", string.punctuation))
    return re.sub(r"\s+", " ", text).strip()

app = FastAPI(title="Clinical Documentation Assistant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://your-frontend-domain.com"],  # tightened for production # fine for local student-project use; TIGHTEN before any real deployment
    allow_methods=["*"],
    allow_headers=["*"],
)

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.realpath(os.path.join(BACKEND_DIR, "..", "frontend"))
SAMPLE_DATA_DIR = os.path.realpath(os.path.join(BACKEND_DIR, "..", "sample_data"))
load_dotenv(os.path.join(BACKEND_DIR, ".env"))


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
        # Accumulate raw audio across chunks so Whisper can transcribe a longer,
        # more coherent window instead of isolated 5-second fragments (which is
        # what produced the garbled, "totally wrong" live transcript).
        self.audio_buffer = bytearray()  # raw PCM only — no WAV headers in here
        self.last_transcribed_len = 0
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
        # slower CPU, queuing every 5s chunk behind a slow transcription call
        # is exactly what causes the delay between speaking and seeing text
        # to keep growing over the course of a recording.
        self.transcribing: bool = False

    @property
    def full_transcript(self):
        return "\n".join(self.transcript_lines)


@app.websocket("/ws/session")
async def live_session(websocket: WebSocket):
    await websocket.accept()
    session = LiveSession()
    livekit_bridge = None
    await websocket.send_json({"type": "session_started", "session_id": session.session_id})

    async def handle_livekit_audio(audio_bytes: bytes):
        """Adapt LiveKit's PCM chunks to the existing Whisper session logic."""
        try:
            from .transcription import (
                transcribe_audio_bytes,
                is_silent_wav,
                extract_pcm_from_wav,
                wav_bytes_from_pcm,
            )

            if is_silent_wav(audio_bytes) or session.transcribing:
                return
            pcm, sample_rate, sample_width, channels = extract_pcm_from_wav(audio_bytes)
            session.sample_rate = sample_rate
            session.sample_width = sample_width
            session.channels = channels
            session.audio_buffer.extend(pcm)
            max_bytes = 10 * sample_rate * sample_width * channels
            if len(session.audio_buffer) > max_bytes:
                del session.audio_buffer[: len(session.audio_buffer) - max_bytes]

            session.transcribing = True
            await websocket.send_json({"type": "transcribing"})
            wav_blob = wav_bytes_from_pcm(
                bytes(session.audio_buffer), sample_rate, sample_width, channels
            )
            recent_context = " ".join(session.transcript_lines[-3:])
            text = await asyncio.to_thread(
                transcribe_audio_bytes,
                wav_blob,
                prompt=f"{CLINICAL_VOCAB_HINT} Clinical consultation. {recent_context}"[-1000:],
            )
            if not text:
                return

            from .soap_generator import _infer_speaker

            key = _normalize_for_dedup(text)
            if key and key not in session.emitted_keys:
                session.emitted_keys.add(key)
                inferred_speaker = _infer_speaker(text)
                if inferred_speaker:
                    session.last_speaker = inferred_speaker
                speaker = (session.last_speaker or "patient").capitalize()
                line = f"{speaker}: {text}"
                session.transcript_lines.append(line)
                await websocket.send_json({
                    "type": "transcript_update",
                    "line": line,
                    "line_index": len(session.transcript_lines) - 1,
                    "full_transcript": session.full_transcript,
                })
            await websocket.send_json({
                "type": "note_update",
                "note": generate_soap(session.full_transcript, use_llm=False),
            })
        except Exception as error:
            logger.exception("LiveKit audio transcription failed")
            try:
                await websocket.send_json({"type": "error", "message": str(error)})
            except WebSocketDisconnect:
                pass
        finally:
            session.transcribing = False

    try:
        while True:
            message = await websocket.receive()

            if message.get("type") == "websocket.disconnect":
                break

            if "bytes" in message and message["bytes"] is not None:
                # Audio chunk arrived — accumulate it and transcribe a longer window
                try:
                    from .transcription import (
                        transcribe_audio_bytes,
                        is_silent_wav,
                        extract_pcm_from_wav,
                        wav_bytes_from_pcm,
                    )
                    audio_bytes = message["bytes"]

                    if is_silent_wav(audio_bytes):
                        continue

                    # Each incoming chunk is a *complete* WAV file (header + PCM),
                    # produced fresh by the frontend every 5s. We must strip that
                    # header before appending — buffering whole WAV files back to
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

                    # Keep the last ~10 seconds of *raw PCM* for context. With the
                    # busy-guard below protecting against a growing backlog, we can
                    # afford a bit more context than the bare minimum — more audio
                    # around an unclear word gives Whisper a better chance of
                    # recognizing it correctly instead of guessing from a too-short
                    # clip.
                    session.audio_buffer.extend(pcm)
                    max_bytes = 10 * sample_rate * sample_width * channels
                    if len(session.audio_buffer) > max_bytes:
                        del session.audio_buffer[: len(session.audio_buffer) - max_bytes]

                    # If a transcription pass from a previous chunk is still
                    # running, don't start another one on top of it — that's
                    # what causes calls to pile up and the on-screen delay to
                    # keep growing over the course of a recording. The audio
                    # we just buffered above will simply be included in the
                    # next pass once the current one finishes.
                    if session.transcribing:
                        continue
                    session.transcribing = True

                    # Re-wrap the accumulated PCM into ONE valid WAV file right
                    # before transcribing — never buffer whole WAV files.
                    wav_blob = wav_bytes_from_pcm(
                        bytes(session.audio_buffer), sample_rate, sample_width, channels
                    )

                    recent_context = " ".join(session.transcript_lines[-3:])
                    await websocket.send_json({"type": "transcribing"})
                    try:
                        text = await asyncio.to_thread(
                            transcribe_audio_bytes,
                            wav_blob,
                            prompt=f"{CLINICAL_VOCAB_HINT} Clinical consultation. {recent_context}"[-1000:],
                        )
                        if not text:
                            continue

                        from .soap_generator import _infer_speaker

                        # Only treat text ending in . ! or ? as a "complete" sentence
                        # worth emitting. Whatever trails the last terminator is kept
                        # as pending_tail instead of being emitted immediately — it
                        # may still change once more audio arrives, and emitting it
                        # early is what caused near-duplicate, slightly-different
                        # lines to pile up in the transcript.
                        complete_sentences = [s.strip() for s in re.findall(r"[^.!?]+[.!?]+", text) if s.strip()]
                        last_terminator_end = 0
                        for m in re.finditer(r"[.!?]+", text):
                            last_terminator_end = m.end()
                        tail = text[last_terminator_end:].strip()

                        # Whisper doesn't always add terminal punctuation, especially
                        # for short/casual utterances — if we held every unpunctuated
                        # fragment back forever, the transcript (and the pipeline
                        # status indicator) would look permanently "stuck" even
                        # though transcription is working fine. Once a trailing
                        # fragment is long enough (8+ words) it's very unlikely to
                        # still be "in progress", so treat it as complete instead of
                        # waiting on punctuation that may never come.
                        if tail and len(tail.split()) >= 8:
                            complete_sentences.append(tail)
                            tail = ""

                        session.pending_tail = tail

                        for sentence in complete_sentences:
                            key = _normalize_for_dedup(sentence)
                            if not key or key in session.emitted_keys:
                                continue
                            session.emitted_keys.add(key)
                            inferred_speaker = _infer_speaker(sentence)
                            if inferred_speaker:
                                session.last_speaker = inferred_speaker
                            # Preserve the previous role for fragments such as "and it
                            # gets worse" instead of randomly changing speakers.
                            speaker = (session.last_speaker or "patient").capitalize()
                            line = f"{speaker}: {sentence}"
                            session.transcript_lines.append(line)
                            await websocket.send_json({
                                "type": "transcript_update",
                                "line": line,
                                "line_index": len(session.transcript_lines) - 1,
                                "full_transcript": session.full_transcript,
                            })

                        # Always refresh the note and advance the pipeline status
                        # after any non-empty transcription pass — even if this
                        # particular window didn't yield a brand-new sentence. If we
                        # only did this when new sentences appeared, a stretch of
                        # unpunctuated or still-forming speech would leave the
                        # pipeline indicator visibly stuck on "Capture".
                        note = generate_soap(session.full_transcript, use_llm=False)
                        await websocket.send_json({"type": "note_update", "note": note})
                    finally:
                        # Always release the guard, even on an early 'continue'
                        # above or an exception below — otherwise one failed
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
                        note = generate_soap(session.full_transcript, use_llm=False)
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
                    # Flush any trailing sentence fragment that was held back
                    # because it hadn't been terminated by punctuation yet —
                    # otherwise the last thing said before Stop Recording can
                    # be silently dropped from the transcript.
                    tail = session.pending_tail.strip()
                    if tail:
                        from .soap_generator import _infer_speaker
                        key = _normalize_for_dedup(tail)
                        if key and key not in session.emitted_keys:
                            session.emitted_keys.add(key)
                            inferred_speaker = _infer_speaker(tail)
                            if inferred_speaker:
                                session.last_speaker = inferred_speaker
                            speaker = (session.last_speaker or "patient").capitalize()
                            line = f"{speaker}: {tail}"
                            session.transcript_lines.append(line)
                            await websocket.send_json({
                                "type": "transcript_update",
                                "line": line,
                                "line_index": len(session.transcript_lines) - 1,
                                "full_transcript": session.full_transcript,
                            })
                        session.pending_tail = ""

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
                    session.last_transcribed_len = 0
                    session.sample_rate = None
                    session.emitted_keys = set()
                    session.pending_tail = ""
                    session.transcribing = False
                    await websocket.send_json({"type": "reset_ack"})

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

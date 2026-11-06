"""
Speech-to-text using faster-whisper (a fast CTranslate2-based port of OpenAI Whisper),
plus speaker diarization (pyannote.audio) to label who's speaking (Doctor/Patient).

IMPORTANT — first-run behavior:
The first time you transcribe audio, faster-whisper downloads the model weights
from Hugging Face. This requires internet access on whatever machine runs the
backend, and only needs to happen once — the model is cached locally afterward.

LANGUAGE SUPPORT:
Live per-chunk transcription now auto-detects language per chunk (English/Urdu/
mixed), using a MULTILINGUAL model (not the old .en-suffixed English-only models).
Set WHISPER_MODEL to "tiny", "base", or "small" (WITHOUT the ".en" suffix) —
.en models physically cannot recognize Urdu.

SPEAKER DIARIZATION (Doctor vs Patient):
Diarization needs the FULL conversation audio to work (it compares voice
characteristics across the whole recording to tell speakers apart) — it cannot
run on isolated 1-second live chunks. Call `diarize_and_label(...)` once, at
finalize time, on the complete accumulated PCM buffer for the session.
Requires: pip install pyannote.audio, an HF_TOKEN in your environment, and
accepting the license on https://huggingface.co/pyannote/speaker-diarization-3.1
"""

import io
import os
import re
import wave

# Keep speech models on the project's D: drive by default. An explicit HF_HOME
# environment variable still takes precedence for another deployment location.
PROJECT_ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("HF_HOME", os.path.join(PROJECT_ROOT, "models"))

_model = None
_diarization_pipeline = None

def get_model(model_size: str = None):
    """Lazy-loads the Whisper model on first use (avoids slow startup every run).

    NOTE: use a MULTILINGUAL model here (e.g. "tiny", "base", "small") — NOT
    "tiny.en"/"small.en". The ".en" models are English-only and cannot
    transcribe Urdu or auto-detect language at all.
    """
    global _model
    model_size = model_size or os.environ.get("WHISPER_MODEL", "small")
    if model_size.endswith(".en"):
        raise ValueError(
            f"WHISPER_MODEL='{model_size}' is an English-only model and cannot "
            "transcribe Urdu or auto-detect language. Use 'tiny', 'base', or "
            "'small' instead (no '.en' suffix)."
        )
    if _model is None:
        from faster_whisper import WhisperModel
        # "int8" compute type keeps this usable on CPU-only machines (no GPU required)
        _model = WhisperModel(
            model_size,
            device="cpu",
            compute_type="int8",
            cpu_threads=max(1, (os.cpu_count() or 2) - 1),
        )
    return _model


def transcribe_audio_bytes(
    audio_bytes: bytes,
    model_size: str = None,
    prompt: str = "",
    beam_size: int = 5,
    language: str = None,
):
    """
    Transcribes a chunk of WAV audio (as raw bytes) and returns (text, detected_language).

    language=None (default) auto-detects per chunk between whatever languages the
    multilingual model supports (including English and Urdu). Pass language="ur"
    or language="en" to force one, e.g. if you already know which language a
    session is in and want to skip auto-detection overhead.
    """
    model = get_model(model_size)
    audio_buffer = io.BytesIO(audio_bytes)

    segments, info = model.transcribe(
        audio_buffer,
        beam_size=beam_size,
        language=language,  # None = auto-detect (needed for Urdu support)
        vad_filter=True,
        vad_parameters={
            "min_silence_duration_ms": 500,
            "speech_pad_ms": 200,
        },
        condition_on_previous_text=False,
        without_timestamps=True,
        initial_prompt=prompt or "Clinical consultation between a doctor and patient.",
        temperature=0.0,
        no_speech_threshold=0.4,
        compression_ratio_threshold=2.4,
        log_prob_threshold=-1.0,
    )
    text = " ".join(seg.text.strip() for seg in segments if seg.text.strip())
    return text.strip(), info.language  # e.g. ("...", "ur") or ("...", "en")


def transcribe_with_timestamps(audio_bytes: bytes, model_size: str = None, language: str = None):
    """
    Same as transcribe_audio_bytes, but keeps per-segment start/end timestamps.
    Used at finalize time so segments can be aligned to diarization speaker turns.
    Returns a list of dicts: [{"start": float, "end": float, "text": str}, ...]
    """
    model = get_model(model_size)
    audio_buffer = io.BytesIO(audio_bytes)

    segments, info = model.transcribe(
        audio_buffer,
        beam_size=5,
        language=language,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 200},
        condition_on_previous_text=False,
        without_timestamps=False,  # need real timestamps this time
        initial_prompt="Clinical consultation between a doctor and patient.",
        temperature=0.0,
        no_speech_threshold=0.4,
        compression_ratio_threshold=2.4,
        log_prob_threshold=-1.0,
    )
    out = [
        {"start": seg.start, "end": seg.end, "text": seg.text.strip()}
        for seg in segments
        if seg.text.strip()
    ]
    return out, info.language


def get_diarization_pipeline():
    """Lazy-loads the pyannote speaker-diarization pipeline (heavy; loaded once)."""
    global _diarization_pipeline
    if _diarization_pipeline is None:
        from pyannote.audio import Pipeline
        hf_token = os.environ.get("HF_TOKEN")
        if not hf_token:
            raise RuntimeError(
                "HF_TOKEN is not set. Diarization requires a Hugging Face token "
                "with access to pyannote/speaker-diarization-3.1 (see setup docs)."
            )
        _diarization_pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            use_auth_token=hf_token,
        )
    return _diarization_pipeline


def diarize_and_label(pcm_bytes: bytes, sample_rate: int, sample_width: int = 2, channels: int = 1):
    """
    Runs diarization on the FULL accumulated conversation audio and returns
    speaker turns labeled as "Doctor" or "Patient" (best-effort heuristic).

    Returns: [{"start": float, "end": float, "speaker": "Doctor"|"Patient"}, ...]
    """
    import numpy as np
    import torch

    pipeline = get_diarization_pipeline()
    wav_bytes = wav_bytes_from_pcm(pcm_bytes, sample_rate, sample_width, channels)

    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    waveform = torch.from_numpy(samples).unsqueeze(0)  # shape: (1, num_samples)

    diarization = pipeline({"waveform": waveform, "sample_rate": sample_rate})

    raw_turns = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        raw_turns.append({"start": turn.start, "end": turn.end, "speaker": speaker})

    return _label_doctor_vs_patient(raw_turns)


def _label_doctor_vs_patient(turns):
    """
    Best-effort heuristic to map generic diarization labels (SPEAKER_00,
    SPEAKER_01, ...) onto "Doctor" / "Patient". True role identification from
    audio alone isn't solved in general — this uses a reasonable convention:
    in most consultations, the doctor speaks first (greeting/intake) and
    speaks in more, shorter turns (asking questions) rather than long
    single answers. Review/adjust output rather than trusting it blindly.
    """
    if not turns:
        return turns

    raw_labels = sorted({t["speaker"] for t in turns})
    if len(raw_labels) == 1:
        # Only one voice detected; can't distinguish doctor/patient at all.
        for t in turns:
            t["speaker"] = "Speaker"
        return turns

    first_speaker = turns[0]["speaker"]
    turn_counts = {lbl: sum(1 for t in turns if t["speaker"] == lbl) for lbl in raw_labels}
    # Prefer "speaks first AND has more turns" as Doctor; fall back to "speaks first".
    most_turns_speaker = max(turn_counts, key=turn_counts.get)
    doctor_label = first_speaker if first_speaker == most_turns_speaker else first_speaker

    for t in turns:
        t["speaker"] = "Doctor" if t["speaker"] == doctor_label else "Patient"
    return turns


def merge_transcript_with_speakers(segments, speaker_turns):
    """
    Aligns Whisper segments (with timestamps) to diarization speaker turns by
    picking whichever speaker turn has the most time overlap with each segment.
    Returns a list of {"speaker": "Doctor"|"Patient", "text": str} lines, with
    consecutive same-speaker segments merged into one line.
    """
    def overlap(a_start, a_end, b_start, b_end):
        return max(0.0, min(a_end, b_end) - max(a_start, b_start))

    lines = []
    for seg in segments:
        best_speaker, best_overlap = "Unknown", 0.0
        for turn in speaker_turns:
            ov = overlap(seg["start"], seg["end"], turn["start"], turn["end"])
            if ov > best_overlap:
                best_overlap, best_speaker = ov, turn["speaker"]

        if lines and lines[-1]["speaker"] == best_speaker:
            lines[-1]["text"] += " " + seg["text"]
        else:
            lines.append({"speaker": best_speaker, "text": seg["text"]})

    return lines


def extract_pcm_from_wav(wav_bytes: bytes):
    """
    Parses a single valid WAV chunk (as produced by the frontend's
    audioSamplesToWav()) and returns its raw PCM payload plus format info.
    """
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        sample_rate = wf.getframerate()
        sample_width = wf.getsampwidth()
        channels = wf.getnchannels()
        pcm = wf.readframes(wf.getnframes())
    return pcm, sample_rate, sample_width, channels


def wav_bytes_from_pcm(pcm_bytes: bytes, sample_rate: int, sample_width: int = 2, channels: int = 1) -> bytes:
    """Wraps raw PCM bytes back into a single valid WAV file."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def is_silent_wav(audio_bytes: bytes, threshold: int = 120) -> bool:
    """Quick check to skip transcribing silence (saves compute on empty audio chunks)."""
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
            n_frames = wf.getnframes()
            sample_width = wf.getsampwidth()
            frames = wf.readframes(n_frames)
            if not frames:
                return True

            if sample_width == 2:
                import struct
                count = min(len(frames) // 2, 4000)
                samples = struct.unpack(f"<{count}h", frames[: count * 2])
                avg = sum(abs(s) for s in samples) / max(count, 1)
            else:
                chunk = frames[:2000]
                avg = sum(abs(b - 128) for b in chunk) / max(len(chunk), 1)

            return avg < threshold
    except Exception:
        return False
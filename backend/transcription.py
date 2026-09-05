"""
Speech-to-text using faster-whisper (a fast CTranslate2-based port of OpenAI Whisper).

IMPORTANT — first-run behavior:
The first time you transcribe audio, faster-whisper downloads the model weights
(~150MB for the 'base' model) from Hugging Face. This requires internet access on
whatever machine runs the backend, and only needs to happen once — the model is
cached locally afterward (~/.cache/huggingface by default).

If you don't have a mic/audio pipeline ready yet, use the text-mode endpoint
(/api/generate-note) instead — it exercises the exact same SOAP-generation logic
without needing any audio at all. See sample_data/sample_consultation.txt.
"""

import io
import os
import wave

# Keep speech models on the project's D: drive by default. An explicit HF_HOME
# environment variable still takes precedence for another deployment location.
PROJECT_ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("HF_HOME", os.path.join(PROJECT_ROOT, "models"))

_model = None


def get_model(model_size: str = None):
    """Lazy-loads the Whisper model on first use (avoids slow startup every run)."""
    global _model
    # Now that the backend guards against a transcription backlog (see
    # LiveSession.transcribing in main.py — it never starts a new pass while
    # one is still running), it's safe to default back to small.en for
    # noticeably better word accuracy. Worst case on a slow CPU: updates
    # arrive a little less often, not a runaway growing delay. If it still
    # feels too slow for your machine, drop to base.en or tiny.en in .env.
    model_size = model_size or os.environ.get("WHISPER_MODEL", "small.en")
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
) -> str:
    """
    Transcribes a chunk of WAV audio (as raw bytes) and returns the recognized text.
    Expects standard PCM WAV — the frontend's MediaRecorder output is converted to
    this format client-side before sending (see frontend/app.js).
    """
    model = get_model(model_size)
    audio_buffer = io.BytesIO(audio_bytes)

    segments, _info = model.transcribe(
        audio_buffer,
        beam_size=beam_size,
        language="en",
        vad_filter=True,
        vad_parameters={
            # Slightly longer than the old 300ms so a brief pause mid-sentence
            # (patient thinking, doctor pausing before the next question)
            # doesn't get treated as an end-of-speech boundary.
            "min_silence_duration_ms": 500,
            # Pads a bit of audio onto both ends of each detected speech
            # segment. Without this, VAD can clip the very start/end of a
            # word right at the boundary, which is a common cause of
            # first-word or last-word transcription errors.
            "speech_pad_ms": 200,
        },
        # IMPORTANT: conditioning on previous text is a known Whisper *hallucination*
        # trigger — once it emits a plausible-but-wrong phrase ("I can't hold it...",
        # "Thank you..."), that same text conditions the next pass and can repeat or
        # chain. This backend transcribes short rolling windows and already carries its
        # own context via `initial_prompt`, so disabling conditioning removes the loop
        # without losing accuracy. See https://github.com/openai/whisper/discussions/1133
        condition_on_previous_text=False,
        without_timestamps=True,
        initial_prompt=prompt or "Clinical consultation between a doctor and patient.",
        temperature=0.0,
        # Lower than the faster-whisper default so segments that are probably NOT
        # speech (keyboard click, chair squeak, a short cough) get dropped instead of
        # being turned into hallucinated dialogue.
        no_speech_threshold=0.4,
        compression_ratio_threshold=2.4,
        log_prob_threshold=-1.0,
    )
    text = " ".join(seg.text.strip() for seg in segments if seg.text.strip())
    return text.strip()


def extract_pcm_from_wav(wav_bytes: bytes):
    """
    Parses a single valid WAV chunk (as produced by the frontend's
    audioSamplesToWav()) and returns its raw PCM payload plus format info.

    We use this to strip the 44-byte WAV header off *each incoming chunk*
    before buffering, so the accumulated buffer is pure PCM. Concatenating
    whole WAV files (header + data + header + data + ...) produces a blob
    that isn't valid WAV, which is what caused
    'Invalid data found when processing input' — ffmpeg/PyAV choked on the
    stray RIFF header sitting in the middle of the "audio".
    """
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        sample_rate = wf.getframerate()
        sample_width = wf.getsampwidth()
        channels = wf.getnchannels()
        pcm = wf.readframes(wf.getnframes())
    return pcm, sample_rate, sample_width, channels


def wav_bytes_from_pcm(pcm_bytes: bytes, sample_rate: int, sample_width: int = 2, channels: int = 1) -> bytes:
    """Wraps raw PCM bytes back into a single valid WAV file. Call this once,
    right before transcription, on the *accumulated* PCM buffer — never on
    each individual chunk (that's what caused the bug)."""
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

            if sample_width == 2:  # 16-bit signed PCM (what our frontend encoder produces)
                import struct
                count = min(len(frames) // 2, 4000)
                samples = struct.unpack(f"<{count}h", frames[: count * 2])
                avg = sum(abs(s) for s in samples) / max(count, 1)
            else:  # fallback for 8-bit unsigned PCM
                chunk = frames[:2000]
                avg = sum(abs(b - 128) for b in chunk) / max(len(chunk), 1)

            return avg < threshold
    except Exception:
        return False

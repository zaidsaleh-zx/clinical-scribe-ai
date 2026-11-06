"""Preload models needed for transcription + diarization so the WS test's
finalize step runs against a warm cache. Downloads the multilingual Whisper
model and instantiates the pyannote diarization pipeline (which downloads it).
Prints a clear OK/FAIL line for each so the caller can inspect the result.
"""
import os
import sys

os.environ.setdefault("HF_HOME", os.path.join(os.getcwd(), "models"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

# Mirror the backend: backend/.env holds HF_TOKEN (load_dotenv in main.py).
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join("backend", ".env"))
except Exception:
    pass


def main():
    print(">>> downloading Systran/faster-whisper-base (multilingual) ...", flush=True)
    try:
        from huggingface_hub import snapshot_download

        snapshot_download("Systran/faster-whisper-base")
        print("WHISPER_BASE_READY", flush=True)
    except Exception as e:
        print(f"WHISPER_BASE_FAIL: {e!r}", flush=True)

    print(">>> loading pyannote speaker-diarization-3.1 pipeline ...", flush=True)
    try:
        from backend.transcription import get_diarization_pipeline

        get_diarization_pipeline()
        print("PYANNOTE_READY", flush=True)
    except Exception as e:
        print(f"PYANNOTE_FAIL: {e!r}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"PRELOAD_UNHANDLED: {e!r}", flush=True)
        sys.exit(1)
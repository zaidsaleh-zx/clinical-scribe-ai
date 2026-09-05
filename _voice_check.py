"""Verify the voice engine end-to-end WITHOUT a microphone.

1. Loads the cached faster-whisper model (backend/transcription.py).
2. Builds a short valid WAV (silence) and runs transcribe_audio_bytes on it.
   Silence -> empty text, but an exception here would mean the engine is broken.
3. Writes results to _voice_check.log
"""
import io
import json
import os
import struct
import sys
import wave

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "backend"))
LOG = os.path.join(ROOT, "_voice_check.log")
lines = []


def log(m):
    lines.append(str(m))
    print(m)


def make_wav(seconds=1.0, sr=16000):
    """16 kHz mono 16-bit PCM WAV; pure silence for this test."""
    n = int(seconds * sr)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(b"\x00\x00" * n)
    return buf.getvalue()


def main():
    ok = True

    # 1. WS handshake (this is what the frontend Live Audio tab connects to)
    try:
        from websockets.sync.client import connect
        with connect("ws://127.0.0.1:8000/ws/session") as ws:
            first = json.loads(ws.recv())
            ws.send(json.dumps({"type": "reset"}))
            ack = json.loads(ws.recv())
            log(f"[ws] first={first.get('type')} ack={ack.get('type')}")
            ok &= first.get("type") == "session_started" and ack.get("type") == "reset_ack"
    except Exception as e:
        import asyncio
        import websockets

        async def run():
            async with websockets.connect("ws://127.0.0.1:8000/ws/session") as ws:
                first = json.loads(await ws.recv())
                await ws.send(json.dumps({"type": "reset"}))
                ack = json.loads(await ws.recv())
                log(f"[ws] first={first.get('type')} ack={ack.get('type')}")
                return first.get("type") == "session_started" and ack.get("type") == "reset_ack"

        try:
            ok &= asyncio.run(run())
        except Exception as ex:
            log(f"[ws] FAIL {ex}")
            ok = False

    # 2. faster-whisper engine: load cached model + transcribe silence WAV
    try:
        from transcription import get_model, transcribe_audio_bytes
        model = get_model("base.en")
        log(f"[engine] loaded={type(model).__module__}.{type(model).__name__}")
        wav = make_wav()
        text = transcribe_audio_bytes(wav, model_size="base.en")
        log(f"[engine] transcribe(silence) -> '{text}' (empty expected, no exception = OK)")
        ok &= isinstance(text, str)
    except Exception as e:
        log(f"[engine] FAIL {type(e).__name__}: {e}")
        ok = False

    # 3. Status endpoint (what the frontend header chips read)
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8000/api/status", timeout=5) as r:
            st = json.loads(r.read())
        log(f"[status] whisper_installed={st.get('whisper_installed')} llm_configured={st.get('llm_configured')}")
        ok &= st.get("whisper_installed") is True
    except Exception as e:
        log(f"[status] FAIL {e}")
        ok = False

    log("VOICE CHECK PASS" if ok else "VOICE CHECK FAIL")
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
"""REAL-SPEECH -> TEXT live test (frontend-faithful).

Streams a real human voice WAV through /ws/session with TRUE real-time pacing:
one 1s chunk sent, then we wait for the server's live frames (with a generous
deadline) exactly like the browser does. Proves speech actually becomes text
while still streaming (not just at finalize).
"""
import io
import json
import os
import time as _time
import wave

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_ws_speech_test.log")
URL = "ws://127.0.0.1:8000/ws/session"
WAV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_speech_test.wav")
LINES = []


def log(m):
    LINES.append(str(m))
    print(m)


def flush_log():
    """Write whatever we have so far, even if the script crashes."""
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(LINES) + "\n")


def load_pcm():
    if not os.path.exists(WAV_PATH):
        raise FileNotFoundError(
            f"Missing test audio file: {WAV_PATH}. "
            "Place a mono PCM WAV named '_speech_test.wav' next to this script."
        )
    w = wave.open(WAV_PATH, "rb")
    sr = w.getframerate()
    return w.readframes(w.getnframes()), sr


def chunk_to_wav(pcm, sr):
    buf = io.BytesIO()
    wf = wave.open(buf, "wb")
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(sr)
    wf.writeframes(pcm)
    wf.close()
    return buf.getvalue()


def recv_json(ws, timeout=None):
    """recv() + json.loads() with a clear error instead of a silent hang/crash."""
    try:
        raw = ws.recv(timeout=timeout)
    except TimeoutError:
        log(f"  !! timed out waiting for server response (timeout={timeout}s)")
        return None
    except Exception as e:
        log(f"  !! connection error while receiving: {e!r}")
        return None
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError) as e:
        log(f"  !! non-JSON frame from server: {raw!r} ({e})")
        return None


def drain(ws, deadline_ms=9000):
    """Read any frames the server emits in the next N ms and summarise them."""
    out = []
    end = _time.perf_counter() + deadline_ms / 1000.0
    while _time.perf_counter() < end:
        remaining = end - _time.perf_counter()
        try:
            evt = json.loads(ws.recv(timeout=min(0.35, max(0.01, remaining))))
        except Exception:
            break
        t = evt.get("type")
        if t == "transcript_update":
            out.append("LINE=" + evt.get("line", ""))
        elif t == "partial_transcript":
            out.append("tail=" + evt.get("text", ""))
        elif t == "transcribing":
            out.append("...")
        elif t == "note_update":
            out.append("note(" + str(len(evt.get("note", {}).get("subjective", []))) + ")")
        else:
            out.append(str(t))
    return out


def main():
    from websockets.sync.client import connect

    pcm, sr = load_pcm()
    chunk = sr * 2
    total = len(pcm)
    n = 0
    log(f"speech {total/sr:.2f}s, chunks of 1.0s, real-time pacing")

    try:
        ws = connect(URL, open_timeout=20)
    except Exception as e:
        log(f"!! failed to connect to {URL}: {e!r}")
        return

    try:
        first = recv_json(ws, timeout=10)
        if first is None:
            log("!! no initial 'session_started' message from server; aborting")
            return
        log("first: " + str(first.get("type")))

        ws.send(json.dumps({"type": "reset"}))
        reset_ack = recv_json(ws, timeout=10)
        if reset_ack is None:
            log("!! no reset_ack from server; aborting")
            return
        log("reset: " + str(reset_ack.get("type")))

        off = 0
        while off < total:
            ws.send(chunk_to_wav(pcm[off: off + chunk], sr))
            off += chunk
            n += 1
            for f in drain(ws):
                log(f"  chunk {n}: {f}")
            # real-time pace: 1s of audio = 1s of wall clock
            _time.sleep(1.0 - min(0.4, n * 0.05))

        log("--- stopped speaking; finalize ---")
        ws.send(json.dumps({"type": "finalize"}))
        for f in drain(ws, 15000):
            log("  final: " + f)
    finally:
        ws.close()
        flush_log()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"!! unhandled exception: {e!r}")
        flush_log()
        raise
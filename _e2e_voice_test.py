"""Frontend-faithful end-to-end speech->text test against /ws/session.

Mimics exactly what the browser does:
  1. read a 16kHz mono PCM WAV
  2. apply the same DC-blocker + peak normalization as frontend/app.js
  3. package 1s chunks as 16-bit WAV and stream over the WebSocket
  4. drain transcript_update / partial_transcript frames per chunk
  5. send 'finalize', drain, and report committed transcript lines

Success criteria: at least one transcript_update line commits LIVE
(i.e. before 'finalize' is sent) and no error frames are received.
"""
import io
import json
import os
import struct
import time as _time
import wave

from websockets.sync.client import connect

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_e2e_voice_test.log")
WAV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_sapi_test.wav")
URL = "ws://127.0.0.1:8000/ws/session"
LINES = []

# Mirror of frontend/app.js constants
DC_ALPHA = 0.995
LEAD_SILENCE_S = 1.0   # silence BEFORE speech (tests empty-window handling)
TRAIL_SILENCE_S = 1.0  # silence AFTER  speech


def log(m):
    LINES.append(str(m))
    print(m)


def load_and_process():
    """Return a list of 1s chunks (bytes, sr) identical to browser output."""
    w = wave.open(WAV_PATH, "rb")
    sr = w.getframerate()
    sw = w.getsampwidth()
    ch = w.getnchannels()
    raw = w.readframes(w.getnframes())
    w.close()
    assert sw == 2 and ch == 1, f"expected mono 16-bit, got sw={sw} ch={ch}"

    n = len(raw) // 2
    samples = struct.unpack(f"<{n}h", raw)
    floats = [s / 32768.0 for s in samples]

    # One-pole DC blocker (same as frontend)
    dc_prev = 0.0
    dc_last = 0.0
    out = []
    for x in floats:
        y = x - dc_prev + DC_ALPHA * dc_last
        dc_prev = x
        dc_last = y
        out.append(y)

    # Peak normalization (same as frontend normalizeSamples)
    peak = max(abs(v) for v in out) if out else 0.0
    gain = min(3.0, 0.45 / peak) if peak > 1e-4 else 1.0
    if gain > 1.0:
        out = [v * gain for v in out]

    chunk_len = sr  # 1s of audio
    pulse = [0.0] * chunk_len  # 1 full silent second (float samples)
    chunks_pcm = [pulse for _ in range(int(LEAD_SILENCE_S))]
    for i in range(0, len(out), chunk_len):
        chunks_pcm.append(out[i:i + chunk_len])
    chunks_pcm += [pulse for _ in range(int(TRAIL_SILENCE_S))]

    result = []
    for c in chunks_pcm:
        buf = io.BytesIO()
        wf = wave.open(buf, "wb")
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        frames = b"".join(pack16(v) for v in c)
        wf.writeframes(frames)
        wf.close()
        result.append(buf.getvalue())
    return result, sr


def pack16(v):
    v = max(-1.0, min(1.0, v))
    return struct.pack("<h", int(v * 0x8000) if v < 0 else int(v * 0x7FFF))


def recv_json(ws, timeout):
    try:
        raw = ws.recv(timeout=timeout)
    except Exception:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def drain(ws, deadline_ms):
    out = []
    end = _time.perf_counter() + deadline_ms / 1000.0
    while _time.perf_counter() < end:
        try:
            evt = json.loads(ws.recv(timeout=0.3))
        except Exception:
            continue
        t = evt.get("type")
        if t == "transcript_update":
            out.append("LINE=" + evt.get("line", ""))
        elif t == "partial_transcript":
            out.append("tail=" + evt.get("text", ""))
        elif t == "transcribing":
            out.append("...")
        elif t == "note_update":
            out.append(f"note({len(evt.get('note', {}).get('subjective', []))})")
        elif t == "error":
            out.append("ERROR=" + evt.get("message", ""))
        else:
            out.append(str(t))
    return out


def main():
    chunks, sr = load_and_process()
    total_s = sum(len(c) for c in chunks) / (16000 * 2)  # roughly
    log(f"streaming {len(chunks)} x 1s chunks (sr={sr}; ~{total_s:.0f}s incl {LEAD_SILENCE_S}s lead + {TRAIL_SILENCE_S}s trail silence)")

    ws = connect(URL, open_timeout=20, ping_interval=None, ping_timeout=None)
    first = recv_json(ws, 10)
    log("first: " + str(first.get("type") if first else None))

    live_lines = 0
    errors = []
    for i, chunk in enumerate(chunks, 1):
        ws.send(chunk)
        # First chunk also pays for model warm-up + first decode: give it room.
        deadline = 60000 if i == 1 else 12000
        frames = drain(ws, deadline_ms=deadline)
        for f in frames:
            if f.startswith("ERROR="):
                errors.append(f)
            elif f.startswith("LINE="):
                live_lines += 1
                log(f"  chunk {i} (live): {f}")
            elif f.startswith("tail=") and f != "tail=":
                log(f"  chunk {i} (tail): {f}")
        # real-time-ish pacing: 1s audio per chunk
        _time.sleep(min(1.0, max(0.2, 1.0 - i * 0.04)))

    log("--- stopped speaking; finalize ---")
    ws.send(json.dumps({"type": "finalize"}))
    final_frames = drain(ws, deadline_ms=45000)
    final_lines = 0
    for f in final_frames:
        if f.startswith("ERROR="):
            errors.append(f)
        elif f.startswith("LINE="):
            final_lines += 1
            log("  final: " + f)
        elif f.startswith("tail=") and f != "tail=":
            log("  final tail: " + f)
    ws.close()

    log(f"committed lines: live={live_lines} finalize={final_lines} errors={errors}")
    ok = live_lines > 0 and not errors
    log("E2E VOICE TEST PASS" if ok else "E2E VOICE TEST FAIL")
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(LINES) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys, traceback
    try:
        sys.exit(main())
    except Exception:
        log("!! unhandled exception: " + traceback.format_exc())
        with open(LOG, "w", encoding="utf-8") as f:
            f.write("\n".join(LINES) + "\n")
        raise
"""QUIET-MIC simulation: proves Whisper (VAD) converts FAINT speech to text.

Simulates the user's real failure mode — the server log showed:
    "VAD filter removed 00:01.536 of audio"
i.e. Whisper's voice-activity filter wiped a whole quiet chunk.

This test:
  1. Takes the clean SAPI speech WAV
  2. Attenuates it to ~3% amplitude (= a quiet laptop/phone mic)
  3. Applies the SAME normalizeSamples boost as frontend/app.js (now up to 12x)
  4. Streams it through /ws/session exactly like the browser
  5. Reports live committed transcript lines.

PASS = at least one live transcript_update line with no error frames.
"""
import io
import json
import os
import struct
import time as _time
import wave

from websockets.sync.client import connect

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_e2e_quiet_test.log")
WAV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_sapi_test.wav")
URL = "ws://127.0.0.1:8000/ws/session"
LINES = []

DC_ALPHA = 0.995
QUIET_ATTENUATION = 0.03  # simulate a faint mic: 3% amplitude
LEAD_SILENCE_S = 1.0
TRAIL_SILENCE_S = 1.0


def log(m):
    LINES.append(str(m))
    print(m)


def pack16(v):
    v = max(-1.0, min(1.0, v))
    return struct.pack("<h", int(v * 0x8000) if v < 0 else int(v * 0x7FFF))


def load_and_process():
    w = wave.open(WAV_PATH, "rb")
    sr = w.getframerate()
    sw = w.getsampwidth()
    ch = w.getnchannels()
    raw = w.readframes(w.getnframes())
    w.close()
    assert sw == 2 and ch == 1, f"expected mono 16-bit, got sw={sw} ch={ch}"

    n = len(raw) // 2
    samples = struct.unpack(f"<{n}h", raw)
    floats = [s / 32768.0 * QUIET_ATTENUATION for s in samples]

    # One-pole DC blocker (same as frontend)
    dc_prev = 0.0
    dc_last = 0.0
    out = []
    for x in floats:
        y = x - dc_prev + DC_ALPHA * dc_last
        dc_prev = x
        dc_last = y
        out.append(y)

    # EXACT mirror of frontend normalizeSamples (updated 12x cap / 0.6 target)
    peak = max(abs(v) for v in out) if out else 0.0
    if peak > 1e-4:
        gain = min(12.0, 0.6 / peak)
        if gain > 1.0:
            out = [v * gain for v in out]
    log(f"quiet-mic simulation: attenuated x{QUIET_ATTENUATION}, "
        f"pre-gain peak={peak:.4f}")

    chunk_len = sr
    pulse = [0.0] * chunk_len
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
        wf.writeframes(b"".join(pack16(v) for v in c))
        wf.close()
        result.append(buf.getvalue())
    return result


def recv_json(ws, timeout):
    try:
        raw = ws.recv(timeout=timeout)
    except Exception:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def drain(ws, deadline_ms, idle_timeout=2.5):
    """Read frames until `deadline_ms` passes OR the server stays quiet for
    `idle_timeout` seconds — the server answers a 1-2s pass in ~2s, so a fixed
    12s poll would otherwise make every chunk look ~12s slow."""
    out = []
    end = _time.perf_counter() + deadline_ms / 1000.0
    last_frame = _time.perf_counter()
    while _time.perf_counter() < end:
        if _time.perf_counter() - last_frame > idle_timeout:
            break
        try:
            evt = json.loads(ws.recv(timeout=min(0.3, idle_timeout)))
        except Exception:
            continue
        last_frame = _time.perf_counter()
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
    chunks = load_and_process()
    log(f"streaming {len(chunks)} x 1s chunks (quiet speech)")
    ws = connect(URL, open_timeout=20, ping_interval=None, ping_timeout=None)
    first = recv_json(ws, 10)
    log("first: " + str(first.get("type") if first else None))

    t0 = _time.perf_counter()
    live_lines = 0
    errors = []
    for i, chunk in enumerate(chunks, 1):
        ws.send(chunk)
        # The model is preloaded at startup; silence lead/trail chunks produce no
        # pass and must not stall the drain. Give REAL speech chunks the deadline.
        deadline = 3000 if i in (1, len(chunks)) else 12000
        frames = drain(ws, deadline_ms=deadline)
        for f in frames:
            if f.startswith("ERROR="):
                errors.append(f)
            elif f.startswith("LINE="):
                live_lines += 1
                lag = _time.perf_counter() - t0 - (i - 1)  # elapsed minus stream position
                log(f"  chunk {i} (live): {f}   [line for 1s of audio ~{lag:.1f}s after it was sent]")
        _time.sleep(min(1.0, max(0.2, 1.0 - i * 0.04)))

    log("--- finalize ---")
    ws.send(json.dumps({"type": "finalize"}))
    for f in drain(ws, deadline_ms=45000):
        if f.startswith("ERROR="):
            errors.append(f)
        elif f.startswith("LINE="):
            live_lines += 1
            log("  final: " + f)
    ws.close()

    log(f"committed lines: live={live_lines} errors={errors}")
    ok = live_lines > 0 and not errors
    log("QUIET-MIC TEST PASS" if ok else "QUIET-MIC TEST FAIL")
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(LINES) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    import traceback

    try:
        sys.exit(main())
    except Exception:
        log("!! unhandled exception: " + traceback.format_exc())
        with open(LOG, "w", encoding="utf-8") as f:
            f.write("\n".join(LINES) + "\n")
        raise
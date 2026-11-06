"""Live WebSocket latency probe for the patched incremental transcription pipeline."""
import io
import json
import os
import struct
import time
import wave

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_ws_latency_test.log")
URL = "ws://127.0.0.1:8000/ws/session"
LINES = []


def log(m):
    LINES.append(str(m))
    print(m)


def make_noise_wav(seconds, sr, amp):
    n = int(seconds * sr)
    buf = io.BytesIO()
    wf = wave.open(buf, "wb")
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(sr)
    frames = bytearray()
    i = 0
    while i < n:
        x = (i * 2654435761) % (2 ** 32)
        x = (x ^ (x >> 16)) * 2246822519 % (2 ** 32)
        v = ((x >> 16) / 32767.0) - 1.0
        frames += struct.pack("<h", int(amp * v))
        i +=  1
    wf.writeframes(bytes(frames))
    wf.close()
    return buf.getvalue()


def main():
    from websockets.sync.client import connect

    chunks = []
    i =  0
    while i < 8:
        chunks.append(make_noise_wav(2.0, 16000, 450))
        i +=  1

    ws = connect(URL)
    try:
        first = json.loads(ws.recv())
        log("first: " + str(first.get("type")))
        ws.send(json.dumps({"type": "reset"}))
        ack = json.loads(ws.recv())
        log("reset: " + str(ack.get("type")))

        rtts = []
        partials = 0
        t0 = time.perf_counter()
        i =  0
        while i < len(chunks):
            ws.send(chunks[i])
            deadline = time.perf_counter() + 15.0
            while time.perf_counter() < deadline:
                try:
                    evt = json.loads(ws.recv(timeout=1.0))
                except Exception:
                    break
                if evt.get("type") == "partial_transcript":
                    partials +=  1
                    break
                if evt.get("type") == "note_update":
                    break
            rtts.append(time.perf_counter() - t0)
            log("chunk " + str(i + 1) + " -> " + str(int(rtts[-1] * 1000)) + " ms")
            i +=  1

        total = time.perf_counter() - t0
        needed = len(chunks) * 2.0
        status = "KEEPS UP"
        if total >= needed:
            status = "FALLING BEHIND"
        log("total for " + str(len(chunks)) + " chunks: " + str(round(total, 2)) + "s (cadence needs " + str(needed) + "s; " + status + ")")

        ws.send(json.dumps({"type": "finalize"}))
        fin = json.loads(ws.recv())
        log("finalize -> " + str(fin.get("type")))
    finally:
        ws.close()

    log("partial_transcript frames received: " + str(partials))
    verdict = "WS LATENCY TEST PASS"
    if partials ==  0:
        verdict = "WS LATENCY TEST WARN - no partials"
    log(verdict)
    f = open(LOG, "w", encoding="utf-8")
    f.write("\n".join(LINES) + "\n")
    f.close()


if __name__ == "__main__":
    main()
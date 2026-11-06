"""Validate the 1-second streaming cadence (word-by-word UX).

Sends 12 x 1s WAV chunks (the new AUDIO_CHUNK_MS=1000 cadence) and reports
every frame received per chunk — especially how often a fresh
partial_transcript arrives — so we can confirm the transcript updates about
once per second while speaking. Writes _ws_cadence_test.log."""
import io
import json
import os
import struct
import time
import wave

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_ws_cadence_test.log")
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
        i += 1
    wf.writeframes(bytes(frames))
    wf.close()
    return buf.getvalue()


def main():
    from websockets.sync.client import connect

    chunks = []
    i = 0
    while i < 12:
        chunks.append(make_noise_wav(1.0, 16000, 450))
        i += 1

    ws = connect(URL)
    try:
        first = json.loads(ws.recv())
        log("first: " + str(first.get("type")))
        ws.send(json.dumps({"type": "reset"}))
        ack = json.loads(ws.recv())
        log("reset: " + str(ack.get("type")))

        partials = 0
        updates = 0
        t0 = time.perf_counter()
        i = 0
        while i < len(chunks):
            ws.send(chunks[i])
            deadline = time.perf_counter() + 6.0
            frames_this_chunk = []
            while time.perf_counter() < deadline:
                try:
                    evt = json.loads(ws.recv(timeout=1.0))
                except Exception:
                    break
                t = evt.get("type")
                frames_this_chunk.append(t)
                if t == "partial_transcript":
                    partials += 1
                elif t == "transcript_update":
                    updates += 1
                elif t == "note_update":
                    pass
                if t == "partial_transcript":
                    # after the live tail is pushed, the pass is done — move on
                    break
            log("chunk " + str(i + 1) + " (+" + str(int((time.perf_counter() - t0) * 1000)) + " ms) frames=" + ",".join(frames_this_chunk))
            i += 1

        total = time.perf_counter() - t0
        needed = len(chunks)
        status = "KEEPS UP"
        if total >= needed:
            status = "FALLING BEHIND"
        log("total for " + str(len(chunks)) + " x 1s chunks: " + str(round(total, 2)) + "s (speech time " + str(needed) + "s; " + status + ")")
        log("partial tails: " + str(partials) + ", committed lines: " + str(updates))

        ws.send(json.dumps({"type": "finalize"}))
        fin = json.loads(ws.recv())
        log("finalize -> " + str(fin.get("type")))
    finally:
        ws.close()

    verdict = "CADENCE OK (" + str(partials) + " live tails)"
    log(verdict)
    f = open(LOG, "w", encoding="utf-8")
    f.write("\n".join(LINES) + "\n")
    f.close()


if __name__ == "__main__":
    main()
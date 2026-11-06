"""Unit test for the overlap-guard transcript emitter (the 'same words again
and again' fix). Exercises _append_or_extend_transcript directly against a
fake WebSocket so no server/audio is needed.

Cases:
  1. New sentence is a FULLER version of the last line -> REPLACE in place.
  2. New sentence is a SHORTER re-decode of the last line    -> DROP.
  3. Brand-new sentence                                        -> APPEND.
  4. Exact duplicate (same key)                               -> DROP.
"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from backend.main import LiveSession, _append_or_extend_transcript  # noqa: E402


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, obj):
        self.sent.append(obj)


def run(session, ws, sentence):
    asyncio.run(_append_or_extend_transcript(session, ws, sentence))


def main():
    ok = True

    # Case 1: extension in place
    ws = FakeWS()
    s = LiveSession()
    run(s, ws, "patient has a headache")
    run(s, ws, "patient has a headache and a fever for three days")
    lines = s.transcript_lines
    print("lines:", lines)
    print("last msg:", ws.sent[-1])
    if len(lines) != 1:
        ok = False
    if not lines or "fever for three days" not in lines[-1]:
        ok = False
    if ws.sent[-1].get("replace") is not True:
        ok = False

    # Case 2: shorter re-decode dropped
    before = len(ws.sent)
    run(s, ws, "patient has a headache")
    if len(s.transcript_lines) != 1:
        ok = False
    if len(ws.sent) != before:
        ok = False

    # Case 3: fresh sentence appended
    run(s, ws, "I also feel dizzy when I stand up quickly")
    if len(s.transcript_lines) != 2:
        ok = False
    if ws.sent[-1].get("replace") is not False:
        ok = False

    # Case 4: exact duplicate dropped
    before = len(ws.sent)
    run(s, ws, "I also feel dizzy when I stand up quickly")
    if len(s.transcript_lines) != 2:
        ok = False
    if len(ws.sent) != before:
        ok = False

    print("TRANSCRIPT EMIT: " + ("PASS" if ok else "FAIL"))
    for i, line in enumerate(s.transcript_lines):
        print(f"  [{i}] {line}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
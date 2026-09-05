"""Quick WebSocket handshake test against the running backend.

Connects to /ws/session, expects a session_started frame, sends reset, expects ack.
Writes results to _ws_test.log.
"""
import json
import os

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_ws_test.log")
URL = "ws://127.0.0.1:8000/ws/session"
lines = []
ok = True

try:
    from websockets.sync.client import connect  # websockets >= 12

    def run():
        with connect(URL) as ws:
            msg = json.loads(ws.recv())
            lines.append(f"first frame: {msg}")
            if msg.get("type") != "session_started" or not msg.get("session_id"):
                return False
            ws.send(json.dumps({"type": "reset"}))
            ack = json.loads(ws.recv())
            lines.append(f"reset ack: {ack}")
            return ack.get("type") == "reset_ack"

    ok = run()
except ImportError:
    import asyncio
    import websockets

    async def run():
        async with websockets.connect(URL) as ws:
            msg = json.loads(await ws.recv())
            lines.append(f"first frame: {msg}")
            if msg.get("type") != "session_started" or not msg.get("session_id"):
                return False
            await ws.send(json.dumps({"type": "reset"}))
            ack = json.loads(await ws.recv())
            lines.append(f"reset ack: {ack}")
            return ack.get("type") == "reset_ack"

    ok = asyncio.run(run())
except Exception as e:
    lines.append(f"FAIL: {e}")
    ok = False

lines.append("WS TEST PASS" if ok else "WS TEST FAIL")
print("\n".join(lines))
with open(LOG, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
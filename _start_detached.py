"""Launch the backend server fully detached from any console (Windows).

Uses DETACHED_PROCESS so the server survives the exiting shell and no
console window is created (no window-CLOSE kill like Start-Process).
Prints the child PID and exits.
"""
import os
import subprocess
import sys

PROJECT = os.path.dirname(os.path.abspath(__file__))
# Prefer the dedicated backend venv that actually has uvicorn/faster-whisper.
PYTHON = None
for candidate in (
    os.path.join(PROJECT, ".venv-1", "Scripts", "python.exe"),
    os.path.join(PROJECT, "backend", "venv", "Scripts", "python.exe"),
    os.path.join(PROJECT, ".venv", "Scripts", "python.exe"),
):
    if os.path.exists(candidate):
        PYTHON = candidate
        break
if not PYTHON:
    print("no venv python found", file=sys.stderr)
    sys.exit(1)

out = open(os.path.join(PROJECT, "_server_out.log"), "ab", buffering=0)
err = open(os.path.join(PROJECT, "_server_err.log"), "ab", buffering=0)

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

proc = subprocess.Popen(
    [
        PYTHON, "-m", "uvicorn", "backend.main:app",
        "--host", "0.0.0.0", "--port", "8000",
        # Long keepalive so slow CPU Whisper passes never trip the websocket
        # ping/pong watchdog (some non-browser clients can't answer pings while
        # a pass is decoding). Browsers are unaffected.
        "--ws-ping-interval", "3600",
        "--ws-ping-timeout", "3600",
    ],
    cwd=PROJECT,
    stdout=out,
    stderr=err,
    creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
    close_fds=True,
)
print("DETACHED PID:", proc.pid)
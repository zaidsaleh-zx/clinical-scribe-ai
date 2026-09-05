"""Dev check: single quick HTTP probe to one free OpenRouter model."""
import json
import os
import urllib.request

from dotenv import load_dotenv

load_dotenv("backend/.env")
key = os.environ.get("OPENROUTER_API_KEY", "").strip()

body = json.dumps({
    "model": "google/gemma-4-31b-it:free",
    "max_tokens": 20,
    "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
}).encode()

req = urllib.request.Request(
    "https://openrouter.ai/api/v1/chat/completions",
    data=body,
    method="POST",
    headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "clinical-scribe-ai-dev-check",
    },
)
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        result = json.loads(r.read().decode())
    print("HTTP", r.status)
    print("content:", result["choices"][0]["message"]["content"])
except Exception as e:
    print("PROBE FAILED:", type(e).__name__, str(e)[:300])
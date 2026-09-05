"""Dev check: verify OpenRouter LLM mode works with the project's own key (writes progress to file)."""
import os

from dotenv import load_dotenv

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_openrouter_live.log")
out = open(LOG, "w", encoding="utf-8")

def log(msg):
    print(msg)
    out.write(msg + "\n")
    out.flush()

load_dotenv("backend/.env")
key = os.environ.get("OPENROUTER_API_KEY", "").strip()
log(f"Key loaded: {key[:15]}... (len={len(key)})")

from openai import OpenAI

client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key, timeout=12.0)

models = [
    "google/gemma-4-31b-it:free",
    "z-ai/glm-5.2:free",
    "minimax/minimax-m3:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
]

for model in models:
    log(f"Trying {model} ...")
    try:
        response = client.chat.completions.create(
            model=model,
            max_tokens=30,
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
        )
        content = response.choices[0].message.content
        log(f"OK  {model}: {content!r}")
        break
    except Exception as e:
        log(f"FAIL {model}: {type(e).__name__}: {str(e)[:200]}")

out.close()
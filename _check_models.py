"""Dev check: list currently free OpenRouter models (uses no API key for listing)."""
import json
import urllib.request

req = urllib.request.Request(
    "https://openrouter.ai/api/v1/models",
    headers={"User-Agent": "clinical-scribe-ai-dev-check"},
)
with urllib.request.urlopen(req, timeout=30) as r:
    data = json.loads(r.read().decode())

free_ids = []
by_id = {}
for m in data.get("data", []):
    mid = m.get("id", "")
    by_id[mid] = m
    pricing = m.get("pricing") or {}
    prompt = pricing.get("prompt", "0") or "0"
    completion = pricing.get("completion", "0") or "0"
    is_free = str(prompt) == "0" and str(completion) == "0"
    if is_free or mid.endswith(":free"):
        free_ids.append(mid)

print("TOTAL MODELS:", len(data.get("data", [])))
print("FREE-LIKE COUNT:", len(free_ids))
print("--- FREE MODEL IDS ---")
for mid in free_ids:
    print(mid)

print("--- CANDIDATES PRESENT? ---")
candidates = [
    "google/gemma-4-31b-it:free",
    "google/gemma-4-27b-it:free",
    "google/gemma-3-27b-it:free",
    "z-ai/glm-5.2:free",
    "z-ai/glm-4.6:free",
    "minimax/minimax-m3:free",
    "mini-max/minimax-m3:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3.8-27b:free",
    "deepseek/deepseek-chat-v3-0324:free",
]
for c in candidates:
    print(f"{c}: {'PRESENT' if c in by_id else 'MISSING'}")
    if c in by_id:
        p = by_id[c].get("pricing") or {}
        print(f"    pricing prompt={p.get('prompt')} completion={p.get('completion')}")
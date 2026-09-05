"""Test LLM mode with the free OpenRouter model."""
import json
import urllib.request

sample = """Doctor: Good morning, what brings you in today?
Patient: I've been having this headache for about three days now.
Doctor: Your blood pressure is 128/82, temperature is 100.4 F.
Doctor: It looks like a viral infection. I'll prescribe paracetamol 500mg.
"""

req = urllib.request.Request(
    "http://localhost:8000/api/generate-note",
    method="POST",
    headers={"Content-Type": "application/json"},
    data=json.dumps({"transcript": sample, "use_llm": True}).encode(),
)
try:
    r = urllib.request.urlopen(req, timeout=60)
    result = json.loads(r.read().decode())
    print("HTTP", r.status)
    print("Engine:", result.get("engine"))
    print("Subjective:", result.get("subjective"))
    print("Objective:", result.get("objective"))
    print("Assessment:", result.get("assessment"))
    print("Plan:", result.get("plan"))
except Exception as e:
    print(f"ERROR: {e}")
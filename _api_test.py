"""Live API smoke tests for the running ClinicalScribe AI backend.

Assumes the server is already running on http://127.0.0.1:8000.
Writes results to _api_test.log.
"""
import json
import os
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_api_test.log")
LINES = []


def log(msg):
    LINES.append(str(msg))
    print(msg)


def req(method, path, payload=None, timeout=30):
    url = BASE + path
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        body = resp.read()
        return resp.status, resp.headers, body


def main():
    ok = True

    # 1. health
    try:
        s, h, b = req("GET", "/api/health")
        log(f"[health] {s} {b.decode()}")
        ok &= s == 200 and json.loads(b) == {"status": "ok"}
    except Exception as e:
        log(f"[health] FAIL {e}")
        ok = False

    # 2. status
    try:
        s, h, b = req("GET", "/api/status")
        status = json.loads(b)
        log(f"[status] {s} backend={status.get('backend')} whisper_installed={status.get('whisper_installed')} llm_configured={status.get('llm_configured')} provider={status.get('llm_provider')}")
        ok &= s == 200 and status.get("backend") == "ready"
    except Exception as e:
        log(f"[status] FAIL {e}")
        ok = False

    # 3. sample transcript
    try:
        s, h, b = req("GET", "/api/sample-transcript")
        t = json.loads(b)["transcript"]
        log(f"[sample-transcript] {s} {len(t)} chars")
        ok &= s == 200 and len(t) > 50
        transcript = t
    except Exception as e:
        log(f"[sample-transcript] FAIL {e}")
        ok = False
        transcript = "Doctor: What brings you in today? Patient: I have had a headache for three days and a fever."

    # 4. generate-note (rule-based)
    try:
        s, h, b = req("POST", "/api/generate-note", {"transcript": transcript, "use_llm": False})
        note = json.loads(b)
        sections = {k: len(note.get(k, [])) for k in ("subjective", "objective", "assessment", "plan", "unclassified")}
        log(f"[generate-note rule] {s} engine={note.get('engine')} sections={sections} issues={len(note.get('issues', []))}")
        ok &= s == 200 and all(k in note for k in ("subjective", "objective", "assessment", "plan", "unclassified", "engine", "completeness", "issues"))
    except Exception as e:
        log(f"[generate-note rule] FAIL {e}")
        ok = False

    # 5. export-pdf
    try:
        s, h, b = req("POST", "/api/export-pdf", {
            "note": {
                "subjective": ["test"], "objective": [], "assessment": [], "plan": [],
                "unclassified": [], "engine": "test", "completeness": {}, "issues": [],
            },
            "patient": {"name": "Jane Doe", "age": "34", "gender": "F", "chief_complaint": "headache"},
        })
        log(f"[export-pdf] {s} {len(b)} bytes magic={b[:5]}")
        ok &= s == 200 and b[:4] == b"%PDF"
    except Exception as e:
        log(f"[export-pdf] FAIL {e}")
        ok = False

    # 6. sessions save + list + get
    try:
        s, h, b = req("POST", "/api/sessions", {
            "transcript": transcript,
            "note": {"subjective": ["test"], "objective": [], "assessment": [], "plan": [], "unclassified": [], "engine": "test", "completeness": {}, "issues": []},
            "patient": {"name": "Jane Doe", "age": "34", "gender": "F", "chief_complaint": "headache"},
        })
        sid = json.loads(b)["session_id"]
        log(f"[sessions POST] {s} session_id={sid}")
        s2, _, b2 = req("GET", "/api/sessions")
        session_list = json.loads(b2)
        log(f"[sessions list] {s2} total_saved={len(session_list)}")
        s3, _, b3 = req("GET", f"/api/sessions/{sid}")
        d = json.loads(b3)
        log(f"[sessions get] {s3} patient={d.get('patient', {}).get('name')}")
        ok &= s == 200 and s2 == 200 and s3 == 200
    except Exception as e:
        log(f"[sessions] FAIL {e}")
        ok = False

    # 7. path traversal -> 404
    try:
        s, h, b = req("GET", "/../backend/main.py")
        log(f"[traversal /../backend/main.py] {s} (expected 404)")
        ok &= s == 404
    except urllib.error.HTTPError as e:
        log(f"[traversal /../backend/main.py] HTTP {e.code} (expected 404)")
        ok &= e.code == 404
    except Exception as e:
        log(f"[traversal] FAIL {e}")
        ok = False

    # 8. frontend
    try:
        s, h, b = req("GET", "/")
        html = b.decode("utf-8", "replace")
        log(f"[index] {s} title_found={'Scribe' in html or 'Clinical' in html} len={len(html)}")
        ok &= s == 200 and "<html" in html.lower()
        s2, _, _ = req("GET", "/static/app.js")
        log(f"[static/app.js] {s2}")
        ok &= s2 == 200
        s3, _, _ = req("GET", "/app.js")
        log(f"[root/app.js] {s3}")
    except Exception as e:
        log(f"[frontend] FAIL {e}")
        ok = False

    log("")
    log("=== ALL PASS ===" if ok else "=== SOME CHECKS FAILED ===")
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(LINES) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
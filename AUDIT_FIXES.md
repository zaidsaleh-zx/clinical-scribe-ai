# Audit — fixes applied

This documents every change made to the project from the full line-by-line audit,
plus recommendations for things not implemented here (out of scope for a direct code
fix, but worth knowing about before this goes anywhere near real patients).

## 🔴 Critical — fixed

1. **Leaked live API key.** `backend/.env.example` (uploaded as a plain file named `_env`)
   had a real OpenRouter key pasted into it. Removed. **You must still manually revoke/
   regenerate that key** at https://openrouter.ai/keys — a fix to the file doesn't undo
   the exposure of a key that was already shared. Never put a real key in any `.example`
   file; only your real, git-ignored `backend/.env` should ever hold one.

2. **Path traversal in the static file route** (`backend/main.py`, `frontend_files`).
   `os.path.join(FRONTEND_DIR, filename)` didn't guard against `..` segments in the URL,
   letting a request walk outside `frontend/` and read other files on the host that the
   process could access. Fixed by resolving the path with `os.path.realpath` and
   rejecting anything that doesn't stay inside `FRONTEND_DIR`, plus rejecting slashes in
   the filename outright. Verified with a live traversal attempt — see server output
   in-conversation (now returns 404 instead of file contents).

3. **Invalid Anthropic model ID** (`backend/soap_generator.py`). `"claude-sonnet-4"` is
   not a real model string — it has no snapshot date and isn't a valid alias, so every
   direct-Anthropic call would fail and silently fall back to rule-based. Fixed to
   `claude-sonnet-4-6` by default, overridable via a new `ANTHROPIC_MODEL` env var.

## 🟠 Confirmed functional bug — fixed

4. **Dead default OpenRouter free-tier model.** The old default,
   `meta-llama/llama-3.3-70b-instruct:free`, is confirmed dead by your own test logs
   (404, "unavailable for free"). Replaced the single hardcoded slug with a **fallback
   chain**: `_call_openrouter` now tries a short list of candidate models in order
   (paid Llama 3.3 70B, then two currently-active free models) and only falls back to
   rule-based if *all* of them fail. `OPENROUTER_MODEL` can still override this
   entirely — now accepts a comma-separated list. Free-tier slugs on OpenRouter rotate
   without notice, so a fallback chain is meaningfully more robust than a single default
   going forward, not just a one-time patch.

## 🟡 Regex/classification bugs — fixed

All of these silently reduced classification accuracy without ever throwing an error,
which is why none were caught by existing tests:

- `ddepression` (double-d typo) in `ASSESSMENT_DIAGNOSIS_TERMS` → fixed to `depression`.
  This pattern could never match real text before.
- `artial` → fixed to `atrial` (cardiac rhythm term; context — flutter/brady/tachy —
  makes clear this was the intent).
- `lesomeprazole` → fixed to `esomeprazole`.
- `metocarbam` → fixed to `methocarbamol`.
- `alexe` → fixed to `aleve`.
- `dislok\b` → fixed to `dislocat` (no trailing boundary, consistent with the existing
  `diagnos` prefix-match pattern elsewhere in the same list — now correctly matches
  "dislocation", "dislocated", etc.).
- Removed stray `\bridge\b` from `SUBJECTIVE_SYMPTOM_TERMS` — not a clinical term, looked
  like leftover noise, and could false-positive on unrelated words.

Rule-based output on the original sample transcript was re-verified after these changes
and produces the same correct classification as before — these were all latent/dormant
bugs, not regressions.

## 🟡 Hardening added

5. **Input size limits** (`backend/models.py`). `TranscriptRequest.transcript` now has a
   `max_length=20000` and `min_length=1`. Patient info fields capped at reasonable
   lengths. Prevents very large pastes from causing slow multi-pass regex work or
   confusing downstream LLM context-limit errors, and gives a clean 422 instead.
6. **`/api/health` endpoint** added — useful for uptime checks, container orchestration
   healthchecks, or just confirming the backend is up before a demo without hitting the
   heavier `/api/status` logic.
7. **Structured logging** — added `logging` config in `main.py`; the WebSocket audio
   error path now uses `logger.exception(...)` (full traceback) instead of only sending
   the error string to the client, so server-side debugging doesn't depend on a client
   being connected to see it.

## Verified after fixes

- Backend boots cleanly (`uvicorn main:app`).
- `/api/health` → `200 {"status": "ok"}`.
- `/api/status` → correctly reports `llm_configured: false` when no key is set.
- Path traversal attempt (`/../main.py` and URL-encoded variants) → `404`, source is not
  leaked.
- Oversized transcript → `422` instead of being silently accepted.
- Rule-based SOAP generation on the sample transcript still produces identical, correct
  output to the pre-fix version.

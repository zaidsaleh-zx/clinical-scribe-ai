# What this project should have — recommendations

Not implemented in this pass (they're bigger changes than a direct fix, or are genuine
design decisions you should make deliberately rather than have made for you), but worth
knowing about — especially for a final-year report or viva, where "here's what I'd add
with more time" is exactly the kind of self-aware section that scores well.

## Security & compliance

- **Secret-scanning pre-commit hook.** The leaked key that started this audit is exactly
  what tools like `gitleaks` or `detect-secrets` catch automatically before a commit ever
  happens. A two-line pre-commit config would have caught it at the source. Worth adding
  regardless of project size — it's a five-minute setup that prevents a real incident.
- **Authentication.** Right now anyone who can reach the backend can read every saved
  session (`GET /api/sessions/{id}` has no auth check at all) and generate/export notes.
  Fine for a solo local demo; not fine the moment this is deployed anywhere reachable by
  more than one person. Even a simple API-key-in-header check would close this for a
  student project; a real deployment would want proper user accounts.
- **PHI handling if this ever touches real data.** The README already says this isn't
  for real patients, and that's the right call for a demo — but it's worth stating
  explicitly in the report *why*: LLM mode sends the full raw transcript to a third-party
  API (Anthropic or OpenRouter) with no de-identification step, and SQLite has no
  encryption at rest. Either of those would need addressing before this could touch real
  PHI (HIPAA / equivalent local regulation depending on where it's deployed).
- **Tighten CORS before any non-local deployment.** `allow_origins=["*"]` is explicitly
  and correctly flagged in the code comment as local-only; just don't forget to actually
  change it if this ever leaves your machine.

## Testing

- **A golden test set for the classifier.** The README mentions this as a stretch goal,
  and it's the single highest-value addition for a "results" section: a small set of
  de-identified sample transcripts with hand-labeled correct SOAP sections, run through
  `generate_soap_rule_based`, scored for precision/recall per section. This would have
  caught the `ddepression` / `artial` / `dislok` typos immediately, since they're the
  kind of silent accuracy bug that only shows up when you check *content*, not just that
  the API returns 200.
- **Unit tests for `soap_generator.py` directly** (not just the Playwright end-to-end
  tests already described in the README) — e.g. feed `_classify_line` specific strings
  and assert the expected section, including the negation-handling and tie-breaking edge
  cases the code already goes out of its way to handle correctly.
- **CI.** A GitHub Actions workflow running `pytest` + a linter (`ruff` or `flake8`) on
  every push would have caught the syntax-safe-but-semantically-dead regex typos via a
  classification test, and catches regressions going forward for free.

## Product / UX additions worth considering

- **Per-line confidence or source highlighting.** Since the rule-based engine is a
  scoring system, it already has a numeric "how sure was I" signal internally
  (`plan_strength`, `objective_strength`, etc.) that's currently discarded once a section
  is chosen. Surfacing even a rough confidence indicator per bullet (e.g. a subtle
  visual marker on borderline classifications) would let a clinician's eye go straight to
  the lines most worth double-checking — a genuinely useful, demo-able feature.
- **Structured/interoperable export.** PDF is good for a human to read; a real
  ambient-scribe product would also offer a machine-readable export (FHIR-shaped JSON, or
  even just a clean JSON schema) so the note could theoretically flow into an EHR. Doesn't
  need real EHR integration — just the export format — to be a meaningful addition to a
  portfolio piece.
- **Real speaker diarization**, already flagged as a stretch goal in the README — worth
  keeping on the list; alternating-by-turn is a known simplification.
- **Undo/redo on the editable SOAP sections**, since edits currently overwrite
  `currentNote[section]` with no history — a single-level undo would be cheap to add and
  meaningfully reduces the risk of an accidental edit loss during a real editing session.

## Operational

- **Rate limiting** on `/api/generate-note`, especially once LLM mode is live — an
  unthrottled endpoint that calls a paid API is a cost-control gap as much as a security
  one.
- **Config validation on startup** — e.g. warn in the logs if `ANTHROPIC_API_KEY` is set
  but doesn't start with `sk-ant-`, or if `OPENROUTER_API_KEY` doesn't start with
  `sk-or-`. Cheap to add, saves a confusing silent-fallback debugging session exactly
  like the one that prompted this audit.
- **Retry/backoff for LLM calls** — right now a single transient network blip on either
  provider immediately falls all the way back to rule-based rather than retrying once.
  Reasonable for a demo; a short exponential backoff (1–2 retries) would make LLM mode
  meaningfully more reliable in practice.
- **Docker packaging** — a `Dockerfile` + `docker-compose.yml` would make "clone and run"
  trivial for anyone evaluating the project (examiners included), and sidesteps the
  Python-version/dependency-install friction entirely.

None of these are required for the project to work or to be demoed well as-is — the
core SOAP-generation logic and the honesty-first engine-transparency design are already
the strongest parts of this project. These are the next-tier improvements that separate
"working student project" from "something closer to production-shaped."

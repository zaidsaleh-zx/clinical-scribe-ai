# Project improvements — current status

This file is kept as a truthful follow-up list. Features already implemented are separated
from work that still needs evaluation or a deliberate product decision.

## Implemented in this update

- Protected API routes with a short-lived signed, `HttpOnly`, `SameSite=Strict` browser
  session, login throttling, trusted-origin checks for cookie-authenticated writes, and
  authenticated live-audio WebSockets. Production deployments fail closed without a
  sufficiently long `CLINICAL_SCRIBE_API_KEY`.
- Explicit clinician review before medication or laboratory suggestions become draft
  queue entries. The text extractor filters common negations and requires action language,
  but it is still heuristic and must not be used as a prescribing decision.
- Permanent saved-consultation deletion, including associated notes, orders, and audit
  entries. A patient record is retained while it still has another consultation.
- Versioned JSON note export and undo/redo for section edits.
- Synthetic unit/API tests for authentication, CORS-origin checks, order confirmation,
  order extraction, note classification, and deletion. GitHub Actions runs tests and
  selected lint checks.
- Split dependency groups for core, audio, LLM, and diarization installs; production
  deployments install only the feature sets they need.
- A synchronization script and CI check for the shared backend/frontend copied into
  `hf-space/`.
- Updated deployment, privacy, setup, and project-scope documentation.

## Still worth doing

- Build a clinician-reviewed, appropriately de-identified evaluation set. Measure
  section-level precision/recall, omissions, and source-link accuracy for each engine;
  include error analysis rather than a single accuracy percentage.
- Do not display a numeric confidence score until it has been calibrated against that
  evaluation set. Current source links are traceability, not confidence.
- Add real multi-user identity and per-user record isolation before supporting more than one
  administrator. The shared-key session is a prototype gate only.
- Decide on a retention policy and persistent encrypted storage for any future deployment.
  The current app supports manual deletion but cannot erase backups or data already sent to
  a provider.
- Consider a standards-backed FHIR export only after defining and validating the relevant
  resource profiles. The current JSON download is a versioned project-specific format, not
  FHIR.
- Keep review of generated draft orders mandatory. The extractor cannot validate allergies,
  dosage safety, interactions, or whether an order is clinically indicated.
- Add browser-driven end-to-end coverage for the microphone, responsive layouts, and
  accessibility once a stable browser test environment is available.

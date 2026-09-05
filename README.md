# 🩺 Scribe — AI Clinical Documentation Assistant

An assistant that listens to (or reads) a doctor-patient consultation and drafts a
structured SOAP note in real time — so the doctor spends less time typing and more time
looking at the patient.

Built for: BS IT / AI final year project, healthcare-tech portfolio piece, or a genuinely
useful prototype of a real product category (ambient clinical documentation — companies like
Nuance DAX and Abridge do this commercially).

---

## ⚠️ Important scope note (read this first)

This is a **documentation drafting aid**, not a diagnostic tool. It never tells a patient
what's wrong with them and never makes a treatment decision. Every note it generates is
meant to be **reviewed and edited by the clinician** before it goes anywhere near a real
patient record — the UI deliberately labels every note "DRAFT — review before saving" to
reflect that. Frame it this way in your report/viva: it's solving a *time* problem
(paperwork), not a *diagnostic* problem.

---

## 1. What It Actually Does

1. A consultation happens (either typed/pasted as text, or spoken live into a mic)
2. The transcript streams onto the left side of the screen as it's captured, with speaker
   icons (👨‍⚕️ Doctor / 👤 Patient)
3. On the right side, the app auto-drafts a structured **SOAP note**:
   - **S**ubjective — what the patient reports, in their own words
   - **O**bjective — vitals, exam findings, measurements mentioned
   - **A**ssessment — the clinical impression discussed
   - **P**lan — treatment, medication, follow-up
4. Anything the system isn't confident about goes into an "Unclassified" bucket instead of
   being guessed into the wrong section — a doctor can quickly sort those manually
5. A **documentation completeness** panel flags likely-missing information (e.g., "Vital
   signs not documented," "Allergies not documented") — it only ever flags *absence*, never
   invents content to fill gaps
6. Every section is **editable** — click Edit, adjust, click Save, and your edits are what
   gets exported/saved
7. Finished notes can be **copied**, **saved to a session history**, or **exported as a PDF**

### "2.0" additions — professional dashboard features

| Feature | What it does |
|---|---|
| **Patient info card** | Name, age, gender, chief complaint — feeds into the PDF export header |
| **Processing pipeline stepper** | Visual Capture → Transcribe → Analyze → Structure → Ready indicator, so an audience can see what stage things are at during a demo |
| **AI engine status chips** | Header shows whether Whisper and the Claude LLM are actually available, live — no guessing whether a feature will work |
| **Engine transparency tag** | Every note is labeled with which engine actually produced it (rule-based / Claude LLM / rule-based-after-LLM-fallback), so a silent fallback is never mistaken for LLM quality |
| **Completeness bars + review flags** | Per-section documentation completeness, plus specific missing items |
| **Editable SOAP sections** | Click Edit on any section, adjust the bullets, Save — your edits become the record |
| **Demo Mode** | One click: loads sample patient info + transcript, runs the pipeline animation, generates the note — the "microphone isn't working, no problem" fallback for presentations |
| **Session history sidebar** | SQLite-backed; saved consultations are listed and clickable to reload |
| **PDF export** | A clean, printable SOAP note document with patient info and review flags |
| **Copy SOAP** | One click copies a plain-text note (with a manual-copy fallback if the Clipboard API is blocked) |
| **Dark / light mode** | Toggle in the header; dark theme stays teal-tinted rather than generic black, so the brand stays consistent |

### Real bugs found and fixed while building this (good material for your report)

Being upfront about this — it's evidence of iterative engineering, not "worked first try":

- **Regex word-boundary bug**: `\bmg\b` never matched `"500mg"` since digits and letters are
  both "word characters" — no boundary between them. Fixed with `\d+\s?mg\b`.
- **Speaker-heuristic bug**: an early version used speaker as a *scoring boost*, causing
  every line — even plain greetings — to get shoved into some SOAP category instead of
  landing in "Unclassified." Fixed by making speaker only break ties between already-tied
  categories.
- **Contraction bug**: `"I've also been really tired"` didn't match `"I've been"` because
  "also" sat in between. Fixed by normalizing contractions before matching, instead of
  special-casing every phrasing variant.
- **PDF Unicode crash**: the base PDF font doesn't support Unicode em-dashes — the first test
  export crashed on my own header text. Fixed with a `_safe()` text sanitizer applied to all
  dynamic PDF content.
- **Silent-save bug in the Edit UI**: clicking Save replaced the bullet list with a text box
  but never restored the `<ul>` element afterward, so the re-render silently crashed mid
  save. Caught by a Playwright test that checked actual DOM content after the click, not
  just "did the page not error."

### What was actually tested vs. what to verify yourself

Every "2.0" feature was tested with real requests — direct API calls or a headless browser
(Playwright) clicking through the actual UI and checking real DOM content. Specifically
verified: text-mode generation, PDF export (API and an actual browser download), session
save/list/reload, the Edit→Save flow, dark mode, Copy SOAP, and Demo Mode (run 3x
back-to-back to rule out flakiness).

**Not verified end-to-end**: Live Audio mode's actual transcription quality — needs a real
browser + mic + internet, none of which exist in a sandboxed dev environment. The WebSocket
pipeline, silence detection, and WAV encoding are all confirmed correct; only the Whisper
model call itself is unverified here. **Test this yourself before presenting it live.**

---

## 2. Two Modes — and why both exist

| Mode | What it needs | What it's for |
|---|---|---|
| **Text / Demo mode** | Nothing extra — works immediately | Testing the actual "intelligence" (SOAP generation) without needing a mic or model downloads. **Use this for your presentation if you don't want to risk a live mic demo.** |
| **Live Audio mode** | A microphone + `faster-whisper` installed (downloads a speech model on first use, needs internet) | The full ambient-scribe experience — talk, watch the note build itself |

Both modes share the exact same SOAP-generation logic, so anything you test in Text mode
behaves identically in Live mode.

---

## 3. Architecture

```
┌─────────────────┐                    ┌───────────────────┐
│   Browser         │  REST (text)      │   FastAPI backend   │
│   (frontend/)     │ ─────────────────▶│   (backend/main.py)  │
│                    │                    │                     │
│  ┌──────────────┐ │  WebSocket (audio) │  ┌───────────────┐  │
│  │ Transcript    │◀│────────────────── │  │ Whisper STT    │  │
│  │ (live feed)   │ │                    │  │ (live mode)    │  │
│  ├──────────────┤ │                    │  ├───────────────┤  │
│  │ SOAP Note     │◀│────────────────── │  │ SOAP Generator │  │
│  │ (auto-filled) │ │                    │  │ (rule-based /  │  │
│  └──────────────┘ │                    │  │  LLM-assisted) │  │
└─────────────────┘                    │  └───────────────┘  │
                                          └───────────────────┘
```

**Text mode data flow:** paste transcript → `POST /api/generate-note` → SOAP generator
classifies each line → structured JSON back → rendered into the 4 SOAP sections.

**Live mode data flow:** mic audio → browser records in chunks → converted to WAV
client-side → streamed over WebSocket → Whisper transcribes → line appended to running
transcript → SOAP note regenerated from the full transcript so far → both pushed back to
the browser → rendered live.

---

## 4. Project Structure

```
clinical-scribe-ai/
├── backend/
│   ├── main.py              # FastAPI app — REST + WebSocket endpoints, serves frontend
│   ├── soap_generator.py    # the core intelligence: transcript -> structured SOAP note
│   ├── transcription.py     # Whisper speech-to-text wrapper (live mode only)
│   ├── pdf_export.py        # generates the exportable SOAP note PDF
│   ├── db.py                # SQLite-backed session history (save/list/reload)
│   ├── models.py            # request/response schemas
│   └── sessions.db          # created automatically on first save — safe to delete to reset history
├── frontend/
│   ├── index.html           # dashboard: patient card, pipeline stepper, transcript | SOAP note
│   ├── style.css             # design system — light/dark clinical teal theme
│   └── app.js                # all UI logic: modes, editing, history, PDF export, WAV encoding
├── sample_data/
│   └── sample_consultation.txt   # example transcript for Demo Mode
├── requirements.txt
└── README.md
```

---

## 5. How the SOAP Generator Actually Works (`backend/soap_generator.py`)

This is the part worth explaining carefully in your report — it's the actual "AI" in the
project.

### Rule-based mode (default, always available)
Each transcript line is scored against keyword/phrase patterns for all 4 SOAP categories
(e.g., "I've been having…" → subjective; "blood pressure is…" → objective; "I'll
prescribe…" → plan). A few design choices worth calling out:

- **Contractions are normalized first** ("I've" → "I have") so patterns don't need to
  handle every contraction variant separately — this alone fixed several
  misclassifications during testing (see §7).
- **Speaker (doctor/patient) only breaks ties**, it never promotes a line that matched zero
  keywords. Early versions of this logic used speaker as a *scoring boost*, which caused
  every line — including plain greetings like "Good morning, what brings you in today?" —
  to get shoved into some SOAP category. Fixed by making speaker a tie-breaker only.
- **Conservative by design**: a line that doesn't clearly match anything goes to
  "Unclassified" rather than being force-fit into the wrong section. A doctor can glance at
  that bucket and manually sort it — far better than a confidently wrong note.

### LLM-assisted mode (optional, needs an API key)
Sends the transcript to an LLM with a structured prompt asking for the same 4-category JSON
output, giving much better quality (real summarization instead of just line-bucketing).
Two provider options — set **one** of these as an environment variable (never hardcode a
key into any project file):

- **Direct Anthropic API**: `export ANTHROPIC_API_KEY=sk-ant-...` (get one at
  [console.anthropic.com](https://console.anthropic.com))
- **OpenRouter**: `export OPENROUTER_API_KEY=sk-or-...` — routes to a Claude model through
  OpenRouter's OpenAI-compatible endpoint. Defaults to `anthropic/claude-sonnet-4`;
  override with `export OPENROUTER_MODEL=anthropic/claude-haiku-4.5` (or any other
  Claude slug from [openrouter.ai/anthropic](https://openrouter.ai/anthropic)) if you want
  a cheaper/faster model.

If both are set, the direct Anthropic API is tried first. The checkbox in the UI ("Use
LLM") toggles this per request. **Automatically falls back to rule-based** if no key is set
or the API call fails — the app never breaks mid-consultation because of a network hiccup
or an expired key. When a fallback happens, the actual error is printed to the backend
console (not swallowed silently) so it's debuggable — check there first if notes seem to be
using rule-based when you expected the LLM.

**Security note**: API keys are read from environment variables only. Never paste a real key
into a chat, commit it to a file, or include it when sharing this project — treat any key
that's been pasted somewhere as compromised and regenerate it from your provider's dashboard.

---

## 6. Getting Started

### Open the website on Windows

After installing the requirements, double-click `open_clinical_scribe.bat` in the
project folder. It starts the FastAPI server if needed, waits for the health check,
and opens the dashboard at `http://127.0.0.1:8000`. Running it again reuses the
existing server instead of starting a second one.

The launcher uses `.venv\Scripts\python.exe` automatically when that environment
exists. This is important for Live Audio mode because the Whisper dependency must be
installed in the same Python environment that starts Uvicorn.

Live Audio uses LiveKit Cloud for browser audio transport. The backend issues a
short-lived browser token from `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and
`LIVEKIT_API_SECRET`, then subscribes to the room audio and sends PCM chunks to
local `faster-whisper`. Copy `backend/.env.example` to `backend/.env`, enter a
newly generated LiveKit key and secret, and restart the backend. Never expose the
secret in frontend code.

The credentials in the supplied screenshot were exposed and must be revoked;
generate replacement credentials before testing this integration.

### Open it on a phone

Connect the phone and computer to the same Wi-Fi network. Start the app on the
computer, then open `http://192.168.10.9:8000` on the phone. If your computer's
IPv4 address changes, run `ipconfig` and use its new IPv4 address instead.
Allow Python through Windows Firewall on private networks if prompted. Live Audio
mode also requires microphone permission in the phone browser.

### Share a permanent website link

For a link that works on any phone, deploy the project to Render:

1. Push this project to a GitHub repository.
2. In Render, choose **New +** -> **Blueprint** and select the repository.
3. Render detects `render.yaml`, installs the requirements, and publishes an HTTPS URL.
4. Share that Render URL. HTTPS is required for Live Audio microphone access on phones.

The deployment settings are already prepared in `render.yaml`. Add an
`ANTHROPIC_API_KEY` or `OPENROUTER_API_KEY` in Render's environment settings if
you want Claude-assisted notes; rule-based notes work without either key. The
default SQLite session history is local to the running service and may reset when
the host redeploys or restarts.

Vercel deployment is also supported for the HTTP/FastAPI routes through
`api/index.py`. Vercel's serverless runtime does not provide the persistent
WebSocket process required by Live Audio, so use the Render service for the full
LiveKit and Whisper workflow.

### Install
```bash
pip install -r requirements.txt
```
(`faster-whisper` and `anthropic` are optional — only needed for Live Audio mode / LLM mode
respectively. Text mode works with just the first four packages.)

### Run
```bash
# from the project root
uvicorn backend.main:app --reload --port 8000
```
Open **http://localhost:8000** — the backend serves the frontend directly, so this is the
only thing you need to run.

**To use LLM-assisted notes**, create a `.env` file so you don't have to re-type your key
every time you open a new terminal:

```bash
cd backend
cp ../.env.example .env
```
Open `.env` in any text editor and fill in your key:
```
OPENROUTER_API_KEY=sk-or-your-real-key-here
```
(or `ANTHROPIC_API_KEY=sk-ant-...` if you have a direct Anthropic key instead — leave the
other one blank). Save the file, then just run `uvicorn backend.main:app --reload --port 8000` as
normal — it loads `.env` automatically on startup, no `export` needed.

`.env` is already in `.gitignore`, so it's never at risk of being committed — only
`.env.example` (with blank values) is meant to be shared or pushed to a repo.

Then tick "Use Claude LLM" in the UI. The header's status chips will show green/on once the
backend picks up the key — refresh the page if you started the backend after the page was
already open.

*(Prefer not to use a file? `export OPENROUTER_API_KEY=sk-or-...` in your terminal before
starting uvicorn works too — it just needs to be re-run every time you open a new terminal.)*

### Try it (Text mode — works immediately)
1. Click **"Load sample consultation"**
2. Click **"Generate SOAP note"**
3. Watch the transcript populate on the left and the structured note build on the right

### Try it (Live Audio mode — needs setup)
1. `pip install faster-whisper` (first transcription downloads the model — needs internet)
2. Switch to the **"Live Audio"** tab
3. Click **"Start Recording"**, allow mic access, and talk through a mock consultation
4. Watch the transcript and note build in real time

---

## 7. Known Limitations & Honest Notes (good material for your report/viva)

- **Rule-based classification isn't perfect.** During development, several real bugs came
  up and got fixed — e.g., `"500mg"` wasn't matching the `mg` dosage pattern because of a
  regex word-boundary issue (digits and letters are both "word characters," so there's no
  boundary between them), and the speaker-based scoring was originally a *boost* that forced
  every line into some category instead of correctly leaving ambiguous lines unclassified.
  Both are fixed, but expect real, messier consultations to still produce some
  misclassifications — that's *why* the Unclassified bucket and the "DRAFT — review" label
  exist, not an afterthought.
- **Live Audio mode needs a real environment to fully verify.** The WAV conversion, mic
  capture, and WebSocket streaming are implemented correctly against documented Web Audio
  API / FastAPI WebSocket behavior, but they need an actual browser with mic access and
  `faster-whisper`'s model downloaded to run end-to-end — neither is available in a
  sandboxed dev environment. **Test this mode yourself before presenting it live** — Text
  mode is the safer, fully-verified fallback for a live demo.
- **Whisper's first run needs internet** to download model weights (~150MB, one-time,
  cached afterward). If you're demoing somewhere without reliable internet, run a
  transcription once beforehand so the model's already cached.
- **The rule-based generator has no real "understanding"** — it's pattern matching, not
  summarization. It won't paraphrase or compress like the LLM mode would. This is an honest
  trade-off: it works with zero setup and zero cost, at the expense of note quality.

## 8. Accuracy and speaker identity
- Whisper performs speech-to-text; it does not identify speakers. Live Audio automatically
  uses conversation cues: questions, instructions, examinations, and diagnoses favor the
  Doctor; first-person symptoms and body-condition reports favor the Patient. Ambiguous
  fragments retain the last confident role instead of randomly switching.
- For real Doctor/Patient separation, install the optional `pyannote.audio` dependency,
  configure `HUGGINGFACE_TOKEN`, enable `DIARIZATION_ENABLED`, and use a quiet microphone
  setup. This requires additional model downloads and should be evaluated with
  de-identified recordings before clinical use.
- The default English model is `base.en` for responsive CPU live updates. Use
  `WHISPER_MODEL=small.en` when accuracy matters more than latency; it downloads a larger
  model and takes longer per chunk. No offline model guarantees
  every accent or every word under noise, overlap, or speaker playback.

## 9. Good Stretch Goals (still open)
- **ICD-10 code suggestion** based on the Assessment section
- **A real evaluation set** of de-identified consultation transcripts to measure rule-based
  vs. LLM-assisted accuracy quantitatively — strong material for a results section
- **Multi-user auth** if this ever needed to handle more than one clinician's sessions
- **Richer patient records** — the current session history is a flat list; a real system
  would link multiple visits to one patient profile over time
- A small evaluation set of real (de-identified) consultation transcripts to measure
  rule-based vs. LLM-assisted accuracy — genuinely good material for a results section

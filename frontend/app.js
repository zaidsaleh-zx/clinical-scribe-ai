// Use the FastAPI origin when this file is opened directly from disk.
const API_BASE = window.location.protocol === "file:" ? "http://127.0.0.1:8000" : "";
let currentNote = { subjective: [], objective: [], assessment: [], plan: [], unclassified: [] };
let currentTranscript = "";

// ================= THEME =================
const themeToggle = document.getElementById("themeToggle");
const sunIcon = document.getElementById("themeIconSun");
const moonIcon = document.getElementById("themeIconMoon");

// Default HTML is now `data-theme="dark"` — sync icon state on page load so
// the correct icon (moon) is shown immediately without waiting for a click.
function syncThemeIcon() {
  const isDark = document.body.getAttribute("data-theme") === "dark";
  sunIcon.style.display = isDark ? "block" : "none";
  moonIcon.style.display = isDark ? "none" : "block";
}
syncThemeIcon();

themeToggle.addEventListener("click", () => {
  const body = document.body;
  const isDark = body.getAttribute("data-theme") === "dark";
  body.setAttribute("data-theme", isDark ? "light" : "dark");
  sunIcon.style.display = isDark ? "block" : "none";
  moonIcon.style.display = isDark ? "none" : "block";
});

// ================= SYSTEM STATUS =================
async function loadStatus() {
  try {
    const res = await fetch(`${API_BASE}/api/status`);
    const data = await res.json();
    const whisperChip = document.getElementById("chipWhisper");
    const llmChip = document.getElementById("chipLlm");
    const livekitChip = document.getElementById("chipLiveKit");
    whisperChip.classList.add(data.whisper_installed ? "on" : "off");
    llmChip.classList.add(data.llm_configured ? "on" : "off");
    livekitChip.classList.add(data.livekit_verified ? "on" : "off");
    whisperChip.title = data.whisper_installed ? "Whisper installed" : "Whisper not installed — Live Audio mode unavailable";
    llmChip.title = data.llm_configured
      ? `Configured via ${data.llm_provider === "openrouter" ? "OpenRouter" : "Anthropic API"}`
      : "No API key — using rule-based fallback";
    livekitChip.title = data.livekit_verified
      ? "LiveKit transport verified"
      : "LiveKit not configured/verified — audio streams directly to the local Whisper model (no cloud needed)";
  } catch (err) {
    console.error("Status check failed:", err);
  }
}

// ================= MODE SWITCHING =================
const modeTextBtn = document.getElementById("modeTextBtn");
const modeAudioBtn = document.getElementById("modeAudioBtn");
const textModePanel = document.getElementById("textModePanel");
const textModeControls = document.getElementById("textModeControls");
const audioModeControls = document.getElementById("audioModeControls");

modeTextBtn.addEventListener("click", () => switchMode("text"));
modeAudioBtn.addEventListener("click", () => switchMode("audio"));

function switchMode(mode) {
  const isText = mode === "text";
  modeTextBtn.classList.toggle("active", isText);
  modeAudioBtn.classList.toggle("active", !isText);
  textModePanel.classList.toggle("hidden", !isText);
  textModeControls.classList.toggle("hidden", !isText);
  audioModeControls.classList.toggle("hidden", isText);
}

// ================= PROCESSING PIPELINE =================
const PIPELINE_STEPS = ["capture", "transcribe", "analyze", "structure", "ready"];

function setPipelineStep(activeStep) {
  const activeIdx = PIPELINE_STEPS.indexOf(activeStep);
  document.querySelectorAll(".pipeline-step").forEach(el => {
    const idx = PIPELINE_STEPS.indexOf(el.dataset.step);
    el.classList.remove("active", "done");
    if (idx < activeIdx) el.classList.add("done");
    else if (idx === activeIdx) el.classList.add("active");
  });
}

function resetPipeline() {
  document.querySelectorAll(".pipeline-step").forEach(el => el.classList.remove("active", "done"));
}

async function animatePipelineThenRun(asyncFn) {
  setPipelineStep("capture");
  await sleep(150);
  setPipelineStep("transcribe");
  await sleep(150);
  setPipelineStep("analyze");
  const result = await asyncFn();
  setPipelineStep("structure");
  await sleep(150);
  setPipelineStep("ready");
  return result;
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ================= RENDERING =================
function renderNote(note) {
  currentNote = note;

  const fill = (id, items) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (!items || items.length === 0) {
      el.innerHTML = `<li class="empty">—</li>`;
      return;
    }
    const section = el.id.replace("List", "");
    const sources = note.sources?.[section]?.length === items.length
      ? note.sources[section]
      : findFallbackSourceIndices(items);
    el.innerHTML = items.map((item, index) => {
      const sourceIndex = Number.isInteger(sources[index]) ? sources[index] : -1;
      const sourceLabel = sourceIndex >= 0 ? `Jump to transcript line ${sourceIndex + 1}` : "Source unavailable";
      return `<li class="soap-point${sourceIndex >= 0 ? " has-source" : ""}" data-source-index="${sourceIndex}" tabindex="${sourceIndex >= 0 ? "0" : "-1"}" title="${sourceLabel}">${escapeHtml(item)}${sourceIndex >= 0 ? '<span class="source-hint">View source</span>' : ""}</li>`;
    }).join("");
    el.querySelectorAll(".soap-point.has-source").forEach(point => {
      const showSource = () => focusTranscriptLine(Number(point.dataset.sourceIndex));
      point.addEventListener("click", showSource);
      point.addEventListener("keydown", event => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          showSource();
        }
      });
    });
  };
  fill("subjectiveList", note.subjective);
  fill("objectiveList", note.objective);
  fill("assessmentList", note.assessment);
  fill("planList", note.plan);
  fill("unclassifiedList", note.unclassified);

  document.getElementById("unclassifiedSection").style.display =
    (note.unclassified && note.unclassified.length > 0) ? "" : "none";

  const engineLabels = {
    rule_based: "Rule-based engine",
    llm_anthropic: "Claude LLM (Anthropic)",
    llm_openrouter: "Claude LLM (via OpenRouter)",
    rule_based_fallback: "Rule-based (LLM fallback)",
    none: "",
  };
  document.getElementById("engineTag").textContent = engineLabels[note.engine] || "";

  renderCompleteness(note.completeness || {});
  renderReviewItems(note.issues || []);
}

function focusTranscriptLine(lineIndex) {
  const line = document.querySelector(`[data-transcript-index="${lineIndex}"]`);
  if (!line) return;
  line.scrollIntoView({ behavior: "smooth", block: "center" });
  line.classList.remove("source-highlight");
  void line.offsetWidth;
  line.classList.add("source-highlight");
}

function findFallbackSourceIndices(items) {
  const transcriptLines = currentTranscript.split("\n").filter(line => line.trim());
  if (!transcriptLines.length) return [];

  const stopWords = new Set(["the", "and", "are", "for", "from", "has", "have", "in", "is", "it", "of", "on", "or", "patient", "reports", "that", "this", "to", "was", "with"]);
  const getTerms = text => new Set((text.toLowerCase().match(/[a-z0-9]+/g) || []).filter(term => term.length > 2 && !stopWords.has(term)));
  const lineTerms = transcriptLines.map(getTerms);

  return items.map(item => {
    const itemTerms = getTerms(item);
    let bestIndex = 0;
    let bestScore = -1;
    lineTerms.forEach((terms, index) => {
      const score = [...itemTerms].filter(term => terms.has(term)).length;
      if (score > bestScore) {
        bestScore = score;
        bestIndex = index;
      }
    });
    return bestIndex;
  });
}

function renderCompleteness(completeness) {
  document.querySelectorAll(".completeness-row").forEach(row => {
    const key = row.dataset.key;
    const val = completeness[key] ?? 0;
    const fill = row.querySelector(".completeness-fill");
    fill.style.width = val + "%";
    fill.classList.remove("low", "zero");
    if (val === 0) fill.classList.add("zero");
    else if (val < 100) fill.classList.add("low");
  });
}

function renderReviewItems(issues) {
  const container = document.getElementById("reviewItems");
  if (!issues || issues.length === 0) {
    container.innerHTML = "";
    return;
  }
  container.innerHTML = issues.map(i => `<div class="review-item">⚠ ${escapeHtml(i)}</div>`).join("");
}

function renderTranscriptLine(line, sourceIndex = null) {
  const feed = document.getElementById("transcriptFeed");
  const empty = feed.querySelector(".empty-state");
  if (empty) empty.remove();

  const isDoctor = /^doctor\s*:/i.test(line) || /^dr\.?\s*\w*:/i.test(line);
  const div = document.createElement("div");
  div.className = "transcript-line" + (isDoctor ? " speaker-doctor" : "");
  if (sourceIndex !== null) div.dataset.transcriptIndex = sourceIndex;

  const m = line.match(/^([^:]+):\s*(.*)/);
  const icon = isDoctor ? "👨‍⚕️" : "👤";
  if (m) {
    div.innerHTML = `<span class="speaker-icon">${icon}</span><span><span class="speaker">${escapeHtml(m[1])}:</span> ${escapeHtml(m[2])}</span>`;
  } else {
    div.textContent = line;
  }
  feed.appendChild(div);
  feed.scrollTop = feed.scrollHeight;

  const countEl = document.getElementById("transcriptCount");
  const n = feed.querySelectorAll(".transcript-line").length;
  countEl.textContent = `${n} line${n === 1 ? "" : "s"}`;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function clearTranscript() {
  const feed = document.getElementById("transcriptFeed");
  feed.innerHTML = `<div class="empty-state">Transcript will appear here as the consultation happens.</div>`;
  document.getElementById("transcriptCount").textContent = "0 lines";
  currentTranscript = "";
}

function clearNote() {
  renderNote({ subjective: [], objective: [], assessment: [], plan: [], unclassified: [], engine: "none", completeness: {}, issues: [] });
}

// ================= EDITABLE SOAP SECTIONS =================
document.querySelectorAll(".edit-btn").forEach(btn => {
  btn.addEventListener("click", () => toggleEditSection(btn.dataset.edit));
});

function toggleEditSection(section) {
  const list = document.getElementById(`${section}List`);
  const sectionEl = document.querySelector(`.soap-section[data-section="${section}"]`);
  const existingTextarea = sectionEl ? sectionEl.querySelector(".soap-edit-area") : null;

  if (existingTextarea) {
    const lines = existingTextarea.value.split("\n").map(l => l.trim()).filter(Boolean);
    currentNote[section] = lines;
    if (currentNote.sources) currentNote.sources[section] = [];

    // Restore the <ul> element renderNote() expects — it was replaced by the
    // textarea when edit mode was entered, and renderNote() looks it up by ID.
    const newList = document.createElement("ul");
    newList.className = "soap-list";
    newList.id = `${section}List`;
    const hint = sectionEl.querySelector(".soap-edit-hint");
    if (hint) hint.remove();
    existingTextarea.replaceWith(newList);

    const btn = document.querySelector(`.edit-btn[data-edit="${section}"]`);
    if (btn) btn.textContent = "Edit";

    renderNote(currentNote);
    return;
  }

  if (!list) return;

  const items = currentNote[section] || [];
  const textarea = document.createElement("textarea");
  textarea.className = "soap-edit-area";
  textarea.value = items.join("\n");
  list.replaceWith(textarea);

  const hint = document.createElement("div");
  hint.className = "soap-edit-hint";
  hint.textContent = "One point per line. Click Save to commit.";
  textarea.after(hint);

  const btn = document.querySelector(`.edit-btn[data-edit="${section}"]`);
  if (btn) btn.textContent = "Save";

  const saveHandler = () => {
    const lines = textarea.value.split("\n").map(l => l.trim()).filter(Boolean);
    currentNote[section] = lines;
    if (currentNote.sources) currentNote.sources[section] = [];

    // Restore the <ul> element renderNote() expects — it was replaced by the
    // textarea when edit mode was entered, and renderNote() looks it up by ID.
    const newList = document.createElement("ul");
    newList.className = "soap-list";
    newList.id = `${section}List`;
    hint.remove();
    textarea.replaceWith(newList);

    const freshBtn = document.querySelector(`.edit-btn[data-edit="${section}"]`);
    if (freshBtn) {
      freshBtn.textContent = "Edit";
      freshBtn.removeEventListener("click", saveHandler);
      freshBtn.addEventListener("click", () => toggleEditSection(section));
    }

    renderNote(currentNote);
  };
  btn.replaceWith(btn.cloneNode(true)); // strip old listener cleanly
  const freshBtn = document.querySelector(`.edit-btn[data-edit="${section}"]`);
  if (freshBtn) {
    freshBtn.textContent = "Save";
    freshBtn.addEventListener("click", saveHandler, { once: true });
  }
}

// ================= TEXT MODE =================
const generateBtn = document.getElementById("generateBtn");
const demoModeBtn = document.getElementById("demoModeBtn");
const transcriptInput = document.getElementById("transcriptInput");
const useLlmToggle = document.getElementById("useLlmToggle");

async function fetchSampleTranscript() {
  const res = await fetch(`${API_BASE}/api/sample-transcript`);
  const data = await res.json();
  return data.transcript;
}

async function runGeneration(transcript) {
  clearTranscript();
  currentTranscript = transcript;
  transcript.split("\n").filter(l => l.trim()).forEach((line, index) => renderTranscriptLine(line, index));

  const note = await animatePipelineThenRun(async () => {
    const res = await fetch(`${API_BASE}/api/generate-note`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ transcript, use_llm: useLlmToggle.checked }),
    });
    return res.json();
  });
  renderNote(note);
}

generateBtn.addEventListener("click", async () => {
  const transcript = transcriptInput.value.trim();
  if (!transcript) return;
  generateBtn.disabled = true;
  try {
    await runGeneration(transcript);
  } catch (err) {
    alert("Error generating note — is the backend running? " + err.message);
    console.error(err);
  } finally {
    generateBtn.disabled = false;
  }
});

demoModeBtn.addEventListener("click", async () => {
  demoModeBtn.disabled = true;
  try {
    const sample = await fetchSampleTranscript();
    transcriptInput.value = sample;
    document.getElementById("patientName").value = "Demo Patient";
    document.getElementById("patientAge").value = "34";
    document.getElementById("patientGender").value = "Female";
    document.getElementById("chiefComplaint").value = "Headache, fever";
    await runGeneration(sample);
  } catch (err) {
    alert("Demo failed — is the backend running? " + err.message);
  } finally {
    demoModeBtn.disabled = false;
  }
});

// ================= NOTE ACTIONS =================
document.getElementById("copyBtn").addEventListener("click", () => {
  const text = ["subjective", "objective", "assessment", "plan"].map(section => {
    const items = currentNote[section] || [];
    const title = section.toUpperCase();
    const body = items.length ? items.map(i => `- ${i}`).join("\n") : "Not documented";
    return `${title}:\n${body}`;
  }).join("\n\n");

  const btn = document.getElementById("copyBtn");
  const orig = btn.textContent;
  const flashSuccess = () => { btn.textContent = "✓ Copied"; setTimeout(() => { btn.textContent = orig; }, 1500); };

  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(flashSuccess).catch(() => fallbackCopy(text, flashSuccess));
  } else {
    fallbackCopy(text, flashSuccess);
  }
});

function fallbackCopy(text, onSuccess) {
  // Clipboard API can be blocked in some browser contexts (permissions, non-HTTPS, etc.)
  // — fall back to the older execCommand approach so the button still works.
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  try {
    document.execCommand("copy");
    onSuccess();
  } catch (err) {
    alert("Couldn't copy automatically. Please select and copy the note manually.");
  }
  document.body.removeChild(textarea);
}

document.getElementById("exportPdfBtn").addEventListener("click", async () => {
  const patient = getPatientInfo();
  try {
    const res = await fetch(`${API_BASE}/api/export-pdf`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note: currentNote, patient }),
    });
    if (!res.ok) throw new Error("Export failed");
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `soap_note_${(patient.name || "patient").replace(/\s+/g, "_")}.pdf`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    alert("PDF export failed — is the backend running? " + err.message);
  }
});

document.getElementById("saveSessionBtn").addEventListener("click", async () => {
  const patient = getPatientInfo();
  try {
    const res = await fetch(`${API_BASE}/api/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ transcript: currentTranscript, note: currentNote, patient }),
    });
    if (!res.ok) throw new Error("Save failed");
    const btn = document.getElementById("saveSessionBtn");
    const orig = btn.textContent;
    btn.textContent = "✓ Saved";
    setTimeout(() => { btn.textContent = orig; }, 1500);
    loadHistory();
  } catch (err) {
    alert("Save failed — is the backend running? " + err.message);
  }
});

function getPatientInfo() {
  return {
    name: document.getElementById("patientName").value.trim(),
    age: document.getElementById("patientAge").value.trim(),
    gender: document.getElementById("patientGender").value,
    chief_complaint: document.getElementById("chiefComplaint").value.trim(),
  };
}

// ================= SESSION HISTORY SIDEBAR =================
async function loadHistory() {
  try {
    const res = await fetch(`${API_BASE}/api/sessions`);
    const sessions = await res.json();
    const list = document.getElementById("historyList");
    if (!sessions || sessions.length === 0) {
      list.innerHTML = `
        <div class="empty-state-sm">
          <svg width="44" height="44" viewBox="0 0 44 44" fill="none" xmlns="http://www.w3.org/2000/svg" class="empty-illustration">
            <rect x="9" y="6" width="26" height="34" rx="3" stroke="currentColor" stroke-width="1.4"/>
            <path d="M16 6V4.5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2V6" stroke="currentColor" stroke-width="1.4"/>
            <path d="M14 18h6M14 24l3 3 7-7" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M14 32h12" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" opacity="0.5"/>
          </svg>
          <span>No saved consultations yet.</span>
        </div>`;
      return;
    }
    list.innerHTML = sessions.map(s => {
      const date = new Date(s.created_at).toLocaleDateString("en-GB", { day: "2-digit", month: "short" });
      return `
        <div class="history-item" data-id="${s.id}">
          <div class="history-item-name">${escapeHtml(s.patient_name || "Unnamed patient")}</div>
          <div class="history-item-meta">${escapeHtml(s.chief_complaint || "—")} · ${date}</div>
        </div>`;
    }).join("");

    list.querySelectorAll(".history-item").forEach(el => {
      el.addEventListener("click", () => loadSessionById(el.dataset.id));
    });
  } catch (err) {
    console.error("Failed to load history:", err);
  }
}

async function loadSessionById(id) {
  try {
    const res = await fetch(`${API_BASE}/api/sessions/${id}`);
    const session = await res.json();
    if (session.error) return;

    document.getElementById("patientName").value = session.patient_name || "";
    document.getElementById("patientAge").value = session.patient_age || "";
    document.getElementById("patientGender").value = session.patient_gender || "";
    document.getElementById("chiefComplaint").value = session.chief_complaint || "";

    switchMode("text");
    transcriptInput.value = session.transcript || "";
    clearTranscript();
    currentTranscript = session.transcript || "";
    (session.transcript || "").split("\n").filter(l => l.trim()).forEach((line, index) => renderTranscriptLine(line, index));
    renderNote(session.note);
    resetPipeline();
    setPipelineStep("ready");
  } catch (err) {
    console.error("Failed to load session:", err);
  }
}

document.getElementById("refreshHistoryBtn").addEventListener("click", loadHistory);

// ================= LIVE AUDIO MODE =================
// Live Audio streams the microphone DIRECTLY to this backend over the WebSocket as
// 16-bit PCM WAV chunks, where the local faster-whisper model transcribes them.
// No external/cloud transport is required — voice-to-text works as long as the
// backend is reachable and the Whisper model is downloaded.
const recordBtn = document.getElementById("recordBtn");
const resetSessionBtn = document.getElementById("resetSessionBtn");
const recordTimerEl = document.getElementById("recordTimer");
const audioSourceSelect = document.getElementById("audioSourceSelect");

let ws = null;
let mediaStream = null;
let isRecording = false;
let recordSeconds = 0;
let timerInterval = null;
let audioContext = null;
let sourceNode = null;
let processorNode = null;
let pcmBuffer = [];
let audioSendTimer = null;
let captureSampleRate = 16000;

// How often buffered PCM is packaged into a WAV and pushed to the backend.
const AUDIO_CHUNK_MS = 2500;

function connectWebSocket() {
  const backendUrl = API_BASE || window.location.origin;
  const websocketUrl = backendUrl.replace(/^http/, "ws") + "/ws/session";
  ws = new WebSocket(websocketUrl);

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "transcript_update") {
      setPipelineStep("transcribe");
      renderTranscriptLine(msg.line, msg.line_index);
      currentTranscript = msg.full_transcript;
    } else if (msg.type === "transcribing") {
      setPipelineStep("transcribe");
    } else if (msg.type === "note_update") {
      setPipelineStep("ready");
      renderNote(msg.note);
    } else if (msg.type === "error") {
      console.error("Backend error:", msg.message);
      alert(msg.message);
    } else if (msg.type === "reset_ack") {
      clearTranscript();
      clearNote();
      resetPipeline();
    }
  };

  return new Promise((resolve, reject) => {
    ws.onopen = () => resolve();
    ws.onerror = () => reject(new Error("Could not connect to the live transcription service."));
  }).catch(err => {
    console.error("WebSocket connection failed:", err);
    throw err;
  });
}

// Package raw float PCM samples into a standard 16-bit mono WAV file.
// The sample rate is read back from the actual AudioContext so this works even
// if the browser overrode our 16 kHz request (the backend trusts the header).
function audioSamplesToWav(samples, sampleRate) {
  const numFrames = samples.length;
  const buffer = new ArrayBuffer(44 + numFrames * 2);
  const view = new DataView(buffer);

  const writeString = (offset, str) => {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
  };

  writeString(0, "RIFF");
  view.setUint32(4, 36 + numFrames * 2, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);                       // fmt chunk size
  view.setUint16(20, 1, true);                        // PCM format
  view.setUint16(22, 1, true);                        // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);           // byte rate
  view.setUint16(32, 2, true);                        // block align
  view.setUint16(34, 16, true);                       // bits per sample
  writeString(36, "data");
  view.setUint32(40, numFrames * 2, true);

  let offset = 44;
  for (let i = 0; i < numFrames; i++, offset += 2) {
    let s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

function flushAudioToBackend(force = false) {
  // Don't bother the backend with sub-100ms slivers during live capture.
  if (!force && pcmBuffer.length < 1600) return;
  if (!ws || ws.readyState !== WebSocket.OPEN || pcmBuffer.length === 0) {
    pcmBuffer.length = 0;
    return;
  }
  const samples = new Float32Array(pcmBuffer);
  pcmBuffer.length = 0;
  ws.send(audioSamplesToWav(samples, captureSampleRate));
}

async function startDirectAudio() {
  // 16 kHz is our preference (smaller chunks); some browsers ignore the hint,
  // in which case the real sampleRate is written into the WAV header anyway.
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  try {
    audioContext = new AudioCtx({ sampleRate: 16000 });
  } catch (err) {
    audioContext = new AudioCtx();
  }
  captureSampleRate = audioContext.sampleRate || 16000;

  sourceNode = audioContext.createMediaStreamSource(mediaStream);

  // ScriptProcessor is deprecated but works in every browser, including Safari,
  // and needs no separate worklet file (important when served over http).
  processorNode = audioContext.createScriptProcessor(4096, 1, 1);
  processorNode.onaudioprocess = (event) => {
    // Tab capture can be multi-channel; each Float32 sample here is already
    // downmixed by the browser for the mono input buffer.
    const input = event.inputBuffer.getChannelData(0);
    for (let i = 0; i < input.length; i++) pcmBuffer.push(input[i]);
  };
  sourceNode.connect(processorNode);

  // Connect the processor to a zero-gain node (instead of the destination) so
  // it keeps firing in all browsers without feeding the mic back into speakers.
  const silentBus = audioContext.createGain();
  silentBus.gain.value = 0;
  processorNode.connect(silentBus);
  silentBus.connect(audioContext.destination);

  // Stream buffered audio to the backend on a fixed cadence so the transcript
  // and SOAP note update live while the user is speaking.
  audioSendTimer = setInterval(() => flushAudioToBackend(false), AUDIO_CHUNK_MS);
}

function stopDirectAudio() {
  if (audioSendTimer) {
    clearInterval(audioSendTimer);
    audioSendTimer = null;
  }
  if (processorNode) {
    try { processorNode.disconnect(); } catch (err) { /* noop */ }
    processorNode = null;
  }
  if (sourceNode) {
    try { sourceNode.disconnect(); } catch (err) { /* noop */ }
    sourceNode = null;
  }
  if (audioContext) {
    audioContext.close().catch(() => {});
    audioContext = null;
  }
  // Send whatever was captured but not yet flushed (e.g. a short utterance).
  flushAudioToBackend(true);
  pcmBuffer = [];
  if (mediaStream) {
    mediaStream.getTracks().forEach(track => track.stop());
    mediaStream = null;
  }
}

recordBtn.addEventListener("click", async () => {
  if (!isRecording) {
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error("Microphone capture is not supported by this browser.");
      }
      if (audioSourceSelect.value === "browser_tab") {
        if (!navigator.mediaDevices.getDisplayMedia) {
          throw new Error("This browser cannot capture tab audio.");
        }
        mediaStream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
        if (!mediaStream.getAudioTracks().length) {
          mediaStream.getTracks().forEach(track => track.stop());
          throw new Error("No tab audio was shared. Choose a browser tab and enable Share audio.");
        }
        mediaStream.getVideoTracks().forEach(track => { track.onended = () => { if (isRecording) recordBtn.click(); }; });
      } else {
        // Good audio defaults for speech recognition: keep echo cancellation and
        // noise suppression ON so Whisper gets clean speech instead of room echo.
        mediaStream = await navigator.mediaDevices.getUserMedia({
          audio: {
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        });
      }
      if (!ws || ws.readyState !== WebSocket.OPEN) await connectWebSocket();
      await startDirectAudio();
      isRecording = true;
      setPipelineStep("capture");

      recordSeconds = 0;
      timerInterval = setInterval(() => {
        recordSeconds++;
        const m = String(Math.floor(recordSeconds / 60)).padStart(2, "0");
        const s = String(recordSeconds % 60).padStart(2, "0");
        recordTimerEl.textContent = `${m}:${s}`;
      }, 1000);

      recordBtn.classList.add("recording");
      recordBtn.innerHTML = `<span class="rec-dot"></span> Stop Recording`;
    } catch (err) {
      if (mediaStream) {
        mediaStream.getTracks().forEach(track => track.stop());
        mediaStream = null;
      }
      alert("Microphone access denied or unavailable: " + err.message);
    }
  } else {
    isRecording = false;
    clearInterval(timerInterval);
    timerInterval = null;
    stopDirectAudio();
    if (ws && ws.readyState === WebSocket.OPEN) {
      setPipelineStep("analyze");
      // finalize: flush the last sentence fragment held back for punctuation and
      // run the LLM-structured note once at the end for a cleaner final SOAP draft.
      ws.send(JSON.stringify({ type: "finalize" }));
    }
    recordBtn.classList.remove("recording");
    recordBtn.innerHTML = `<span class="rec-dot"></span> Start Recording`;
  }
});

resetSessionBtn.addEventListener("click", () => {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "reset" }));
  } else {
    clearTranscript();
    clearNote();
    resetPipeline();
  }
  recordSeconds = 0;
  recordTimerEl.textContent = "00:00";
});

// ================= 3D MEDICAL BACKGROUND CANVAS =================
// Animated neural-network-style particle web — floating nodes connected
// by lines, evoking a living medical/technology feel behind the UI.
function initMedicalCanvas() {
  const canvas = document.getElementById("medCanvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  let width, height;
  let particles = [];
  const PARTICLE_COUNT = 70;
  const CONNECT_DIST = 160;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function resize() {
    width = canvas.width = window.innerWidth;
    height = canvas.height = window.innerHeight;
  }

  function getColor() {
    const dark = document.body.getAttribute("data-theme") === "dark";
    return {
      node: dark ? "62, 206, 178" : "15, 107, 92",
      line: dark ? "62, 206, 178" : "15, 107, 92",
    };
  }

  function createParticles() {
    particles = [];
    for (let i = 0; i < PARTICLE_COUNT; i++) {
      particles.push({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.5,
        vy: (Math.random() - 0.5) * 0.5,
        r: Math.random() * 2.5 + 1.2,
      });
    }
  }

  function draw() {
    ctx.clearRect(0, 0, width, height);
    const c = getColor();

    // Draw connection lines first
    ctx.lineWidth = 0.6;
    for (let i = 0; i < particles.length; i++) {
      for (let j = i + 1; j < particles.length; j++) {
        const dx = particles[i].x - particles[j].x;
        const dy = particles[i].y - particles[j].y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < CONNECT_DIST) {
          const alpha = (1 - dist / CONNECT_DIST) * 0.22;
          ctx.strokeStyle = `rgba(${c.line}, ${alpha})`;
          ctx.beginPath();
          ctx.moveTo(particles[i].x, particles[i].y);
          ctx.lineTo(particles[j].x, particles[j].y);
          ctx.stroke();
        }
      }
    }

    // Draw nodes with glow
    particles.forEach(p => {
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${c.node}, 0.45)`;
      ctx.shadowColor = `rgba(${c.node}, 0.6)`;
      ctx.shadowBlur = 8;
      ctx.fill();
      ctx.shadowBlur = 0;
    });
  }

  function animate() {
    particles.forEach(p => {
      p.x += p.vx;
      p.y += p.vy;
      if (p.x < -20) p.x = width + 20;
      if (p.x > width + 20) p.x = -20;
      if (p.y < -20) p.y = height + 20;
      if (p.y > height + 20) p.y = -20;
    });
    draw();
    if (!reduceMotion) requestAnimationFrame(animate);
  }

  // Mouse interaction — draw a soft glow ring that attracts nearby nodes
  const mouse = { x: null, y: null };
  window.addEventListener("mousemove", (e) => {
    mouse.x = e.clientX;
    mouse.y = e.clientY;
    // Subtle attract effect on the 30 nearest particles
    particles.forEach(p => {
      const dx = p.x - mouse.x;
      const dy = p.y - mouse.y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < 160 && dist > 0) {
        const force = (160 - dist) / 160 * 0.02;
        p.vx += (dx / dist) * force;
        p.vy += (dy / dist) * force;
      }
    });
  });

  window.addEventListener("resize", () => {
    resize();
    createParticles();
  });

  // Re-theme on dark mode toggle
  const observer = new MutationObserver(() => {
    // Just redraw next frame — colors are read live inside draw()
  });
  observer.observe(document.body, { attributes: true, attributeFilter: ["data-theme"] });

  resize();
  createParticles();
  draw();
  if (!reduceMotion) animate();
}

// ================= INIT =================
loadStatus();
loadHistory();
clearNote();
initMedicalCanvas();

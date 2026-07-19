const startButton = document.querySelector("#start-button");
const form = document.querySelector("#message-form");
const input = document.querySelector("#vendor-message");
const sendButton = document.querySelector("#send-button");
const transcript = document.querySelector("#transcript");
const emptyState = document.querySelector("#empty-state");
const callState = document.querySelector("#call-state");
const liveIndicator = document.querySelector(".live-indicator");
const quoteFacts = document.querySelector("#quote-facts");
const quoteEmpty = document.querySelector("#quote-empty");
const quoteStatus = document.querySelector("#quote-status");
const evidenceCount = document.querySelector("#evidence-count");
const outcomeCard = document.querySelector("#outcome-card");
const outcomeTitle = document.querySelector("#outcome-title");
const outcomeDetail = document.querySelector("#outcome-detail");
const replayButton = document.querySelector("#replay-button");
const modelBadge = document.querySelector("#model-badge");
const suggestions = document.querySelector("#suggestions");
const recordButton = document.querySelector("#record-button");
const recordLabel = document.querySelector("#record-label");
const recordingStatus = document.querySelector("#recording-status");
const competingBidTotal = document.querySelector("#competing-bid-total");
const jobVersion = document.querySelector("#job-version");
const jobTitle = document.querySelector("#job-title");
const jobDetails = document.querySelector("#job-details");
const jobEvidenceSummary = document.querySelector("#job-evidence-summary");

const requestedJobSpecVersionId = new URLSearchParams(window.location.search).get(
  "job_spec_version_id",
);

let callId = null;
let lastAudioEl = null;
let lastReplayUrl = null;
let isBusy = false;
let mediaRecorder = null;
let microphoneStream = null;
let audioChunks = [];
let connectedEstimatorSpec = null;
let jobEvidenceCount = 0;
let quoteEvidenceCount = 0;
let vadContext = null;
let vadTimer = null;
let conversationActive = false;
let discardRecording = false;

const stateOrder = [
  "disclosure",
  "job_presentation",
  "quote_collection",
  "quote_clarification",
  "summary_confirmation",
];

const stateLabels = {
  created: "Created",
  connecting: "Connecting",
  disclosure: "AI disclosure",
  job_presentation: "Presenting job",
  quote_collection: "Collecting quote",
  quote_clarification: "Clarifying terms",
  summary_confirmation: "Confirming summary",
  complete: "Complete quote",
  callback_required: "Callback required",
  declined: "Vendor declined",
  incomplete: "Incomplete quote",
  failed: "Connection failed",
};

const suggestedReplies = {
  disclosure: ["Yes, I can discuss it.", "I need to call you back tomorrow.", "I decline to quote."],
  quote_collection: [
    "We charge a flat price. Labor is $350, stairs are $75, and the total is $475.",
    "It is $120 per hour for a three-person crew.",
  ],
  quote_clarification: [
    "There are no additional fees. The total is binding, and we are available August 1.",
    "It is only an estimate and there may be a $50 equipment fee. We are available that day.",
  ],
  summary_confirmation: ["Yes, that's correct.", "No, the total should be $500."],
};

function reportAutoplayBlocked() {
  recordingStatus.textContent = "Voice is ready. Select Replay buyer voice to hear it.";
}

function rememberForReplay(chunks) {
  if (lastReplayUrl) URL.revokeObjectURL(lastReplayUrl);
  lastReplayUrl = URL.createObjectURL(new Blob(chunks, { type: "audio/mpeg" }));
  replayButton.disabled = false;
}

function appendToSourceBuffer(sourceBuffer, chunk) {
  return new Promise((resolve, reject) => {
    sourceBuffer.addEventListener("updateend", resolve, { once: true });
    sourceBuffer.addEventListener("error", reject, { once: true });
    sourceBuffer.appendBuffer(chunk);
  });
}

// The speech endpoint is a chunked stream without Range support, which a plain
// <audio src> can abort mid-utterance. Consume it with fetch instead: feed
// MediaSource as chunks arrive when the browser supports MP3 MSE, otherwise
// download fully and play the blob.
// Resolves when the element finishes playing (or immediately if playback
// cannot start), so conversation mode knows when to reopen the microphone.
function playbackFinished(audioEl, playPromise) {
  return new Promise((resolve) => {
    audioEl.addEventListener("ended", resolve, { once: true });
    audioEl.addEventListener("error", resolve, { once: true });
    (playPromise ?? audioEl.play()).catch(() => {
      reportAutoplayBlocked();
      resolve();
    });
  });
}

async function playVoice(data) {
  if (!data.audio_url) {
    if (!data.audio_base64) return;
    lastAudioEl = new Audio(`data:${data.audio_content_type};base64,${data.audio_base64}`);
    replayButton.disabled = false;
    await playbackFinished(lastAudioEl);
    return;
  }

  try {
    const response = await fetch(data.audio_url);
    if (!response.ok || !response.body) throw new Error(`Speech request failed (${response.status})`);
    const reader = response.body.getReader();
    const chunks = [];
    const canStream = "MediaSource" in window && MediaSource.isTypeSupported("audio/mpeg");

    if (!canStream) {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value);
      }
      rememberForReplay(chunks);
      lastAudioEl = new Audio(lastReplayUrl);
      await playbackFinished(lastAudioEl);
      return;
    }

    const mediaSource = new MediaSource();
    const mediaUrl = URL.createObjectURL(mediaSource);
    lastAudioEl = new Audio(mediaUrl);
    await new Promise((resolve) => {
      mediaSource.addEventListener("sourceopen", resolve, { once: true });
    });
    const sourceBuffer = mediaSource.addSourceBuffer("audio/mpeg");
    const done = playbackFinished(lastAudioEl);
    for (;;) {
      const { done: finished, value } = await reader.read();
      if (finished) break;
      chunks.push(value);
      await appendToSourceBuffer(sourceBuffer, value);
    }
    if (mediaSource.readyState === "open") mediaSource.endOfStream();
    URL.revokeObjectURL(mediaUrl);
    rememberForReplay(chunks);
    await done;
  } catch (error) {
    recordingStatus.textContent = `Buyer voice failed: ${error.message}`;
  }
}

replayButton.addEventListener("click", () => {
  if (!lastReplayUrl) {
    if (!lastAudioEl) return;
    lastAudioEl.currentTime = 0;
    lastAudioEl.play();
    return;
  }
  lastAudioEl = new Audio(lastReplayUrl);
  lastAudioEl.play();
});

async function api(path, options = {}) {
  const headers = options.body instanceof FormData
    ? { ...options.headers }
    : { "content-type": "application/json", ...options.headers };
  const response = await fetch(path, {
    ...options,
    headers,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail ?? `Request failed (${response.status})`);
  return body;
}

startButton.addEventListener("click", async () => {
  if (isBusy) return;
  if (conversationActive) endConversation("Starting a new call…");
  setBusy(true);
  try {
    if (!requestedJobSpecVersionId) {
      throw new Error(
        "No confirmed Estimator specification was selected. Return to the Estimator and confirm a job first.",
      );
    }
    const total = Number(competingBidTotal.value);
    const activeJobSpecVersionId = requestedJobSpecVersionId;
    const payload = { job_spec_version_id: activeJobSpecVersionId };
    if (Number.isFinite(total) && total > 0) {
      payload.verified_competing_bid = {
            bid_id: `demo_bid_${Date.now()}`,
            source_call_id: "demo_verified_source_call",
            job_spec_version_id: activeJobSpecVersionId,
            total,
            currency: "USD",
            binding_status: "binding",
            evidence_reference: "demo://user-confirmed-competing-bid",
      };
    }
    const data = await api("/api/v1/demo/voice/sessions?tts=stream", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    callId = data.call.call.call_id;
    startButton.querySelector("span").textContent = "Start new call";
    render(data);
    setBusy(false);
    input.focus();
    await playVoice(data);
    // The greeting has been spoken — go straight into hands-free conversation.
    await startConversation();
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(false);
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text || !callId || isBusy) return;
  if (mediaRecorder?.state === "recording") {
    // A typed turn replaces whatever the open microphone captured so far.
    discardRecording = true;
    stopSilenceWatch();
    mediaRecorder.stop();
  }
  setBusy(true);
  input.value = "";
  try {
    const data = await api(`/api/v1/demo/voice/sessions/${callId}/text?tts=stream`, {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    render(data);
    setBusy(false);
    await playVoice(data);
    if (data.terminal) {
      if (conversationActive) endConversation("Call complete.");
    } else if (conversationActive) {
      listenForVendorTurn();
    }
  } catch (error) {
    input.value = text;
    showError(error.message);
  } finally {
    setBusy(false);
    if (!input.disabled) input.focus();
  }
});

// Auto-send after a pause so the vendor never has to click stop mid-conversation.
const VAD_INTERVAL_MS = 100;
const VAD_SILENCE_RMS = 0.012;
const VAD_MIN_SPEECH_MS = 400;
const VAD_SILENCE_HOLD_MS = 1400;

function startSilenceWatch(stream) {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) return;
  vadContext = new AudioContextClass();
  const analyser = vadContext.createAnalyser();
  analyser.fftSize = 2048;
  vadContext.createMediaStreamSource(stream).connect(analyser);
  const samples = new Float32Array(analyser.fftSize);
  let speechMs = 0;
  let silenceMs = 0;
  vadTimer = setInterval(() => {
    analyser.getFloatTimeDomainData(samples);
    let sum = 0;
    for (let i = 0; i < samples.length; i += 1) sum += samples[i] * samples[i];
    const rms = Math.sqrt(sum / samples.length);
    if (rms >= VAD_SILENCE_RMS) {
      speechMs += VAD_INTERVAL_MS;
      silenceMs = 0;
    } else if (speechMs >= VAD_MIN_SPEECH_MS) {
      silenceMs += VAD_INTERVAL_MS;
    }
    if (speechMs >= VAD_MIN_SPEECH_MS && silenceMs >= VAD_SILENCE_HOLD_MS) {
      stopRecording("Pause detected — sending to the buyer agent…");
    }
  }, VAD_INTERVAL_MS);
}

function stopSilenceWatch() {
  if (vadTimer) {
    clearInterval(vadTimer);
    vadTimer = null;
  }
  vadContext?.close().catch(() => {});
  vadContext = null;
}

function stopRecording(statusMessage) {
  stopSilenceWatch();
  if (mediaRecorder?.state === "recording") mediaRecorder.stop();
  recordingStatus.textContent = statusMessage;
}

// Conversation mode: the microphone stays open across turns. A pause sends the
// utterance, the buyer agent answers, and listening resumes when it finishes
// speaking — no clicking between turns.
async function startConversation() {
  if (conversationActive || !callId) return;
  try {
    microphoneStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
    });
  } catch (error) {
    showError(`Microphone unavailable: ${error.message}`);
    return;
  }
  conversationActive = true;
  recordButton.classList.add("recording");
  recordLabel.textContent = "End conversation";
  listenForVendorTurn();
}

function endConversation(statusMessage) {
  conversationActive = false;
  discardRecording = mediaRecorder?.state === "recording";
  stopSilenceWatch();
  if (mediaRecorder?.state === "recording") mediaRecorder.stop();
  microphoneStream?.getTracks().forEach((track) => track.stop());
  microphoneStream = null;
  recordButton.classList.remove("recording");
  recordLabel.textContent = "Start conversation";
  recordingStatus.textContent = statusMessage;
}

function listenForVendorTurn() {
  if (!conversationActive || !microphoneStream) return;
  const preferred = "audio/webm;codecs=opus";
  const options = MediaRecorder.isTypeSupported(preferred) ? { mimeType: preferred } : {};
  mediaRecorder = new MediaRecorder(microphoneStream, options);
  audioChunks = [];
  mediaRecorder.addEventListener("dataavailable", (event) => {
    if (event.data.size) audioChunks.push(event.data);
  });
  mediaRecorder.addEventListener("stop", submitRecording, { once: true });
  mediaRecorder.start(250);
  startSilenceWatch(microphoneStream);
  recordingStatus.textContent = "Listening… speak as the vendor; a pause sends it.";
}

recordButton.addEventListener("click", () => {
  if (!callId) return;
  if (conversationActive) {
    endConversation("Conversation ended. Press Start conversation to resume.");
    return;
  }
  startConversation();
});

async function submitRecording() {
  const mimeType = mediaRecorder?.mimeType || "audio/webm";
  const blob = new Blob(audioChunks, { type: mimeType });
  mediaRecorder = null;
  if (discardRecording) {
    discardRecording = false;
    return;
  }
  if (!blob.size || blob.size < 2000) {
    if (conversationActive) listenForVendorTurn();
    return;
  }

  setBusy(true);
  try {
    const formData = new FormData();
    formData.append("audio", blob, mimeType.includes("webm") ? "vendor.webm" : "vendor-audio");
    const data = await api(`/api/v1/demo/voice/sessions/${callId}/messages?tts=stream`, {
      method: "POST",
      body: formData,
    });
    render(data);
    const latency = data.pipeline_timings_ms?.total;
    recordingStatus.textContent = `Heard: “${data.transcription}”${latency ? ` · ${latency} ms` : ""} — buyer agent is speaking…`;
    setBusy(false);
    await playVoice(data);
    if (data.terminal) {
      endConversation("Call complete — the outcome is on the evidence board.");
      return;
    }
    if (conversationActive) listenForVendorTurn();
  } catch (error) {
    showError(error.message);
    setBusy(false);
    if (conversationActive) listenForVendorTurn();
  }
}

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

document.querySelectorAll(".scenario-guide button").forEach((button) => {
  button.addEventListener("click", () => {
    input.value = button.textContent.replace(/^\d+\.\s*/, "");
    input.focus();
  });
});

function render(data) {
  const view = data.call;
  const status = view.call.status;
  const terminal = data.terminal;
  modelBadge.textContent = data.model;
  renderJob(
    data.confirmed_job_facts,
    view.call.job_spec_version_id,
    connectedEstimatorSpec?.fields ?? {},
  );
  emptyState.hidden = true;
  transcript.hidden = false;
  callState.textContent = stateLabels[status] ?? status;
  liveIndicator.classList.toggle("active", !terminal);
  renderStages(status);
  renderTranscript(view.transcript);
  renderQuote(view.original_quote);
  renderOutcome(view.outcome);
  renderSuggestions(status, terminal);
  input.disabled = terminal;
  sendButton.disabled = terminal;
  recordButton.disabled = terminal;
  input.placeholder = terminal ? "This call has ended." : "Or type the vendor’s reply…";
}

function renderJob(facts = {}, versionId = "demo_spec_piano", evidencedFields = {}) {
  const labels = {
    service: "Service",
    "origin.location": "Pickup",
    origin: "Pickup",
    "destination.location": "Delivery",
    destination: "Delivery",
    requested_date: "Date",
    "item.type": "Item",
    "item.dimensions": "Dimensions",
    "item.weight": "Weight",
    "origin.access": "Pickup access",
    "destination.access": "Delivery access",
    stairs: "Stairs",
  };
  const priority = Object.keys(labels);
  const entries = Object.entries(facts).sort(([left], [right]) => {
    const leftIndex = priority.indexOf(left);
    const rightIndex = priority.indexOf(right);
    return (leftIndex < 0 ? 999 : leftIndex) - (rightIndex < 0 ? 999 : rightIndex);
  });
  jobVersion.textContent = versionId === "demo_spec_piano"
    ? "CONFIRMED JOB · BUILT-IN DEMO"
    : "CONFIRMED JOB · ESTIMATOR";
  jobTitle.textContent = String(facts.service ?? facts["item.type"] ?? "Confirmed job");
  renderEvidenceSummary(evidencedFields);
  jobDetails.replaceChildren();
  entries.forEach(([key, value]) => {
    const row = document.createElement("div");
    const term = document.createElement("dt");
    const description = document.createElement("dd");
    term.textContent = labels[key] ?? key.replaceAll(".", " ").replaceAll("_", " ");
    const valueLine = document.createElement("span");
    valueLine.textContent = String(value);
    description.append(valueLine);
    row.append(term, description);
    jobDetails.append(row);
  });
  jobEvidenceCount = Object.keys(evidencedFields).length;
  updateEvidenceCount();
}

function renderEvidenceSummary(evidencedFields) {
  const groups = new Map();
  Object.values(evidencedFields).forEach((evidence) => {
    const label = describeEvidence(evidence);
    groups.set(label, (groups.get(label) ?? 0) + 1);
  });
  jobEvidenceSummary.replaceChildren();
  jobEvidenceSummary.hidden = groups.size === 0;
  groups.forEach((count, label) => {
    const item = document.createElement("span");
    item.textContent = `${count} ${count === 1 ? "field" : "fields"} · ${label}`;
    jobEvidenceSummary.append(item);
  });
}

function describeEvidence(evidence) {
  const modality = evidence.source?.modality ?? "unknown source";
  const confidence = evidence.confidence ?? "unknown confidence";
  const reference = evidence.source?.reference;
  const sourceLabels = {
    voice: "Voice interview",
    document: reference?.filename === "browser-intake.json"
      ? "Estimator form"
      : "Uploaded document",
    catalog_resolution: "Catalog selection",
  };
  const confidenceLabels = {
    explicit: "directly provided",
    elicited: "answered during intake",
    user_confirmed_suggestion: "user confirmed",
    unknown: "unknown acknowledged",
  };
  return `${sourceLabels[modality] ?? "Intake evidence"} · ${confidenceLabels[confidence] ?? "confirmed"}`;
}

async function loadConnectedEstimatorSpec() {
  if (!requestedJobSpecVersionId) return;
  try {
    connectedEstimatorSpec = await api(
      `/api/v1/intake/specs/${encodeURIComponent(requestedJobSpecVersionId)}`,
    );
    const facts = Object.fromEntries(
      Object.entries(connectedEstimatorSpec.fields).map(([name, evidence]) => [
        name,
        evidence.value,
      ]),
    );
    renderJob(facts, connectedEstimatorSpec.version_id, connectedEstimatorSpec.fields);
    callState.textContent = "Estimator evidence loaded";
  } catch (error) {
    jobTitle.textContent = "Could not load Estimator evidence";
    jobDetails.querySelector("dd").textContent = error.message;
  }
}

if (requestedJobSpecVersionId) {
  jobVersion.textContent = "CONFIRMED JOB · ESTIMATOR CONNECTED";
  jobTitle.textContent = "Estimator specification ready";
  jobDetails.querySelector("dd").textContent = "Select Start test call to load it";
}

loadConnectedEstimatorSpec();

function renderStages(status) {
  const currentIndex = stateOrder.indexOf(status);
  const terminal = ["complete", "callback_required", "declined", "incomplete"].includes(status);
  document.querySelectorAll("#stage-list li").forEach((item, index) => {
    item.classList.toggle("current", index === currentIndex);
    item.classList.toggle("complete", terminal || (currentIndex >= 0 && index < currentIndex));
  });
}

function renderTranscript(events) {
  transcript.replaceChildren();
  events.forEach((event) => {
    const message = document.createElement("article");
    message.className = `message ${event.speaker}`;
    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = event.speaker === "agent" ? "AI" : "V";
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    const header = document.createElement("header");
    const name = document.createElement("strong");
    name.textContent = event.speaker === "agent" ? "Buyer assistant" : "Test vendor";
    const time = document.createElement("time");
    time.textContent = `${Number(event.timestamp_seconds).toFixed(1)}s`;
    const copy = document.createElement("p");
    copy.textContent = event.text;
    header.append(name, time);
    bubble.append(header, copy);
    message.append(avatar, bubble);
    transcript.append(message);
  });
  transcript.scrollTop = transcript.scrollHeight;
}

function renderQuote(quote) {
  quoteFacts.replaceChildren();
  const facts = [];
  quote.line_items.forEach((item) => {
    facts.push({ label: item.category, value: item.description, amount: item.amount == null ? null : `$${Number(item.amount).toFixed(2)}` });
  });
  quote.terms.forEach((term) => {
    facts.push({ label: term.category.replaceAll("_", " "), value: String(term.value), amount: null });
  });
  quoteEmpty.hidden = facts.length > 0;
  quoteStatus.textContent = quote.status === "final" ? "Finalized" : facts.length ? "Capturing" : "Waiting";
  quoteEvidenceCount = facts.length;
  updateEvidenceCount();
  facts.forEach((fact) => {
    const row = document.createElement("div");
    row.className = "fact";
    const content = document.createElement("div");
    const label = document.createElement("span");
    label.textContent = fact.label;
    const value = document.createElement("strong");
    value.textContent = fact.amount ? `${fact.value} · ${fact.amount}` : fact.value;
    const evidence = document.createElement("div");
    evidence.className = "evidence-tag";
    evidence.textContent = "TRANSCRIPT ✓";
    content.append(label, value);
    row.append(content, evidence);
    quoteFacts.append(row);
  });
}

function updateEvidenceCount() {
  if (jobEvidenceCount) {
    evidenceCount.textContent = `${jobEvidenceCount} job · ${quoteEvidenceCount} quote`;
    return;
  }
  evidenceCount.textContent = `${quoteEvidenceCount} ${quoteEvidenceCount === 1 ? "fact" : "facts"}`;
}

function renderOutcome(outcome) {
  outcomeCard.hidden = !outcome;
  if (!outcome) return;
  outcomeTitle.textContent = outcome.outcome_type.replaceAll("_", " ");
  const details = [outcome.reason, ...(outcome.validation_warnings ?? [])].filter(Boolean);
  outcomeDetail.textContent = details.join(" · ") || "Validated and stored with transcript evidence.";
}

function renderSuggestions(status, terminal) {
  suggestions.replaceChildren();
  if (terminal) return;
  (suggestedReplies[status] ?? []).forEach((reply) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = reply;
    button.addEventListener("click", () => {
      input.value = reply;
      input.focus();
    });
    suggestions.append(button);
  });
}

function setBusy(busy) {
  isBusy = busy;
  startButton.disabled = busy;
  if (callId) {
    input.disabled = busy;
    sendButton.disabled = busy;
    // Hanging up must stay possible while the agent is thinking or speaking.
    recordButton.disabled = busy && !conversationActive;
  }
  sendButton.textContent = busy ? "…" : "Send";
}

function showError(message) {
  callState.textContent = `Error: ${message}`;
  liveIndicator.classList.remove("active");
  recordingStatus.textContent = message;
}

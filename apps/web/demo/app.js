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

let callId = null;
let lastAudio = null;
let isBusy = false;
let mediaRecorder = null;
let microphoneStream = null;
let audioChunks = [];

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

function playVoice(data) {
  lastAudio = `data:${data.audio_content_type};base64,${data.audio_base64}`;
  replayButton.disabled = false;
  new Audio(lastAudio).play().catch(() => {
    recordingStatus.textContent = "Voice is ready. Select Replay buyer voice to hear it.";
  });
}

replayButton.addEventListener("click", () => lastAudio && new Audio(lastAudio).play());

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
  setBusy(true);
  try {
    const total = Number(competingBidTotal.value);
    const payload = Number.isFinite(total) && total > 0
      ? {
          verified_competing_bid: {
            bid_id: `demo_bid_${Date.now()}`,
            source_call_id: "demo_verified_source_call",
            job_spec_version_id: "demo_spec_piano",
            total,
            currency: "USD",
            binding_status: "binding",
            evidence_reference: "demo://user-confirmed-competing-bid",
          },
        }
      : {};
    const data = await api("/api/v1/demo/voice/sessions", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    callId = data.call.call.call_id;
    startButton.querySelector("span").textContent = "Start new call";
    render(data);
    playVoice(data);
    input.focus();
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
  setBusy(true);
  input.value = "";
  try {
    const data = await api(`/api/v1/demo/voice/sessions/${callId}/text`, {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    render(data);
    playVoice(data);
  } catch (error) {
    input.value = text;
    showError(error.message);
  } finally {
    setBusy(false);
    if (!input.disabled) input.focus();
  }
});

recordButton.addEventListener("click", async () => {
  if (!callId || isBusy) return;
  if (mediaRecorder?.state === "recording") {
    mediaRecorder.stop();
    recordButton.classList.remove("recording");
    recordLabel.textContent = "Record vendor reply";
    recordingStatus.textContent = "Processing speech with ElevenLabs Scribe v2…";
    return;
  }

  try {
    microphoneStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const preferred = "audio/webm;codecs=opus";
    const options = MediaRecorder.isTypeSupported(preferred) ? { mimeType: preferred } : {};
    mediaRecorder = new MediaRecorder(microphoneStream, options);
    audioChunks = [];
    mediaRecorder.addEventListener("dataavailable", (event) => {
      if (event.data.size) audioChunks.push(event.data);
    });
    mediaRecorder.addEventListener("stop", submitRecording, { once: true });
    mediaRecorder.start();
    recordButton.classList.add("recording");
    recordLabel.textContent = "Stop and send";
    recordingStatus.textContent = "Recording… speak as the vendor, then stop.";
  } catch (error) {
    showError(`Microphone unavailable: ${error.message}`);
  }
});

async function submitRecording() {
  const mimeType = mediaRecorder?.mimeType || "audio/webm";
  const blob = new Blob(audioChunks, { type: mimeType });
  microphoneStream?.getTracks().forEach((track) => track.stop());
  microphoneStream = null;
  mediaRecorder = null;
  if (!blob.size) {
    showError("The microphone recording was empty.");
    return;
  }

  setBusy(true);
  try {
    const formData = new FormData();
    formData.append("audio", blob, mimeType.includes("webm") ? "vendor.webm" : "vendor-audio");
    const data = await api(`/api/v1/demo/voice/sessions/${callId}/messages`, {
      method: "POST",
      body: formData,
    });
    render(data);
    playVoice(data);
    recordingStatus.textContent = `ElevenLabs heard: “${data.transcription}”`;
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(false);
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
  evidenceCount.textContent = `${facts.length} ${facts.length === 1 ? "fact" : "facts"}`;
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
    recordButton.disabled = busy;
  }
  sendButton.textContent = busy ? "…" : "Send";
}

function showError(message) {
  callState.textContent = `Error: ${message}`;
  liveIndicator.classList.remove("active");
  recordingStatus.textContent = message;
}

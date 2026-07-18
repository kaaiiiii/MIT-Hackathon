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
const voiceToggle = document.querySelector("#voice-toggle");
const voiceSelect = document.querySelector("#voice-select");
const replayButton = document.querySelector("#replay-button");
const modelBadge = document.querySelector("#model-badge");
const suggestions = document.querySelector("#suggestions");

let callId = null;
let lastAgentMessage = "";
let isBusy = false;

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

function populateVoices() {
  const voices = window.speechSynthesis?.getVoices() ?? [];
  const previous = voiceSelect.value;
  voiceSelect.replaceChildren();
  const english = voices.filter((voice) => voice.lang.toLowerCase().startsWith("en"));
  const choices = english.length ? english : voices;
  choices.forEach((voice) => {
    const option = document.createElement("option");
    option.value = voice.name;
    option.textContent = `${voice.name} · ${voice.lang}`;
    voiceSelect.append(option);
  });
  if (choices.some((voice) => voice.name === previous)) voiceSelect.value = previous;
}

populateVoices();
if (window.speechSynthesis) window.speechSynthesis.onvoiceschanged = populateVoices;

function speak(text) {
  lastAgentMessage = text;
  replayButton.disabled = false;
  if (!voiceToggle.checked || !window.speechSynthesis) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  const selected = window.speechSynthesis.getVoices().find((voice) => voice.name === voiceSelect.value);
  if (selected) utterance.voice = selected;
  utterance.rate = 0.96;
  utterance.pitch = 1;
  window.speechSynthesis.speak(utterance);
}

replayButton.addEventListener("click", () => lastAgentMessage && speak(lastAgentMessage));
voiceToggle.addEventListener("change", () => {
  if (!voiceToggle.checked && window.speechSynthesis) window.speechSynthesis.cancel();
});

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "content-type": "application/json", ...options.headers },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail ?? `Request failed (${response.status})`);
  return body;
}

startButton.addEventListener("click", async () => {
  if (isBusy) return;
  setBusy(true);
  try {
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    const data = await api("/api/v1/demo/sessions", { method: "POST" });
    callId = data.call.call.call_id;
    startButton.querySelector("span").textContent = "Start new call";
    render(data);
    speak(data.agent_message);
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
    const data = await api(`/api/v1/demo/sessions/${callId}/messages`, {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    render(data);
    speak(data.agent_message);
  } catch (error) {
    input.value = text;
    showError(error.message);
  } finally {
    setBusy(false);
    if (!input.disabled) input.focus();
  }
});

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
  input.placeholder = terminal ? "This call has ended." : "Type the vendor’s reply…";
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
  }
  sendButton.textContent = busy ? "…" : "Send";
}

function showError(message) {
  callState.textContent = `Error: ${message}`;
  liveIndicator.classList.remove("active");
}

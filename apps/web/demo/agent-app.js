const startButton = document.querySelector("#start-button");
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
const modelBadge = document.querySelector("#model-badge");
const competingBidTotal = document.querySelector("#competing-bid-total");
const jobVersion = document.querySelector("#job-version");
const jobTitle = document.querySelector("#job-title");
const jobDetails = document.querySelector("#job-details");
const jobEvidenceSummary = document.querySelector("#job-evidence-summary");
const recordingStatus = document.querySelector("#recording-status");
const widgetHost = document.querySelector("#caller-agent-host");

const requestedJobSpecVersionId = new URLSearchParams(window.location.search).get(
  "job_spec_version_id",
);

let callId = null;
let activeWidget = null;
let latestResponse = null;
let connectedEstimatorSpec = null;
let jobEvidenceCount = 0;
let quoteEvidenceCount = 0;
let ending = false;

const ADVISER_TIMEOUT_MS = 8000;

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

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "content-type": "application/json", ...options.headers },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail ?? `Request failed (${response.status})`);
  return body;
}

async function apiWithTimeout(path, options = {}, timeoutMs = ADVISER_TIMEOUT_MS) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await api(path, { ...options, signal: controller.signal });
  } finally {
    window.clearTimeout(timeout);
  }
}

function fallbackAdvice(error, vendorText = "") {
  const reason = error?.name === "AbortError" ? "adviser_timeout" : "adviser_unavailable";
  return {
    tool_status: "fallback",
    error_code: reason,
    advice_id: null,
    understanding: vendorText
      ? `The vendor said: ${vendorText.slice(0, 240)}`
      : "No vendor text was supplied to the adviser.",
    response_relation: "Respond directly to the vendor's latest statement without repeating prior questions.",
    next_action: "continue_naturally",
    conversational_objective: "Acknowledge the vendor briefly and advance one useful step.",
    response_guidance: "Use one short, natural sentence and at most one question. Do not invent facts, prices, or leverage.",
    should_end_call: false,
    recommended_outcome: null,
    quote_so_far: latestResponse?.call?.original_quote ?? {},
  };
}

startButton.addEventListener("click", async () => {
  startButton.disabled = true;
  ending = false;
  try {
    if (!requestedJobSpecVersionId) {
      throw new Error(
        "No confirmed Estimator specification was selected. Return to the Estimator and confirm a job first.",
      );
    }
    activeWidget?.remove();
    const activeJobSpecVersionId = requestedJobSpecVersionId;
    const payload = { job_spec_version_id: activeJobSpecVersionId };
    const total = Number(competingBidTotal.value);
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
    const data = await api("/api/v1/demo/agent/sessions", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    if (data.voice_provider !== "elevenlabs_agents" || !data.provider_connection_url) {
      throw new Error(
        "ElevenLabs Caller Agent is not configured. Set ELEVENLABS_CALLER_AGENT_ID and restart the API.",
      );
    }
    callId = data.call.call.call_id;
    startButton.querySelector("span").textContent = "Start new call";
    render(data);
    mountAgent(data);
  } catch (error) {
    showError(error.message);
  } finally {
    startButton.disabled = false;
  }
});

function mountAgent(connection) {
  widgetHost.replaceChildren();
  const widget = document.createElement("elevenlabs-convai");
  activeWidget = widget;
  widget.setAttribute("signed-url", connection.provider_connection_url);
  widget.setAttribute("variant", "expanded");
  widget.setAttribute("start-call-text", "Start vendor conversation");
  widget.setAttribute("end-call-text", "End vendor conversation");
  widget.setAttribute("dynamic-variables", JSON.stringify(connection.provider_context));

  widget.addEventListener("elevenlabs-convai:call", (event) => {
    if (!event.detail?.config) {
      showError("ElevenLabs did not provide a client-tool configuration object.");
      return;
    }
    event.detail.config.clientTools = {
      advise_caller_turn: async (parameters) => {
        const vendorText = String(parameters?.vendor_text ?? "").trim();
        if (!vendorText || !callId) return fallbackAdvice(null, vendorText);
        try {
          const data = await apiWithTimeout(`/api/v1/demo/agent/sessions/${callId}/advise`, {
            method: "POST",
            body: JSON.stringify({
              vendor_text: vendorText,
              conversation_history: parameters?.conversation_history ?? null,
            }),
          });
          render(data);
          const advice = data.advice ?? {};
          return {
            tool_status: "ok",
            advice_id: data.advice_id ?? null,
            understanding: advice.understanding ?? vendorText,
            response_relation: advice.response_relation ?? "Address the latest vendor statement.",
            next_action: advice.planned_action ?? "continue_naturally",
            conversational_objective: advice.conversational_objective ?? "Advance the quote conversation by one step.",
            response_guidance: advice.response_guidance ?? "Use one concise, natural response.",
            should_end_call: Boolean(data.recommended_outcome),
            recommended_outcome: data.recommended_outcome ?? null,
            quote_so_far: data.call?.original_quote ?? {},
          };
        } catch (error) {
          console.warn("advise_caller_turn fell back", error);
          recordingStatus.textContent = "GPT advice was slow or unavailable; ElevenLabs continued with safe fallback guidance.";
          return fallbackAdvice(error, vendorText);
        }
      },
      record_caller_utterance: async (parameters) => {
        const spokenText = String(parameters?.spoken_text ?? "").trim();
        if (!spokenText || !callId) {
          return { saved: false, terminal: false, final_outcome: null, error_code: "missing_utterance" };
        }
        try {
          const data = await apiWithTimeout(`/api/v1/demo/agent/sessions/${callId}/utterances`, {
            method: "POST",
            body: JSON.stringify({
              spoken_text: spokenText,
              advice_id: parameters?.advice_id ?? null,
            }),
          });
          render(data);
          return {
            saved: true,
            terminal: data.terminal,
            final_outcome: data.call.outcome?.outcome_type ?? null,
          };
        } catch (error) {
          console.warn("record_caller_utterance could not persist", error);
          return {
            saved: false,
            terminal: false,
            final_outcome: null,
            error_code: error?.name === "AbortError" ? "storage_timeout" : "storage_unavailable",
          };
        }
      },
    };
    recordingStatus.textContent = "Caller tools connected. ElevenLabs can request GPT advice and evidence logging.";
  });

  widget.addEventListener("conversationStarted", () => {
    callState.textContent = "ElevenLabs Agent connected";
    liveIndicator.classList.add("active");
    recordingStatus.textContent = "ElevenLabs is listening and speaking; GPT advises silently between turns.";
  });

  widget.addEventListener("conversationEnded", async () => {
    if (ending || !callId || latestResponse?.terminal) return;
    ending = true;
    try {
      const data = await api(`/api/v1/demo/agent/sessions/${callId}/end`, {
        method: "POST",
        body: "{}",
      });
      render(data);
    } catch (error) {
      showError(error.message);
    } finally {
      ending = false;
    }
  });

  widget.addEventListener("error", (event) => {
    const message = event.detail?.message ?? event.detail?.error?.message ?? "ElevenLabs Agent connection failed";
    showError(String(message));
  });

  widgetHost.append(widget);
}

function render(data) {
  latestResponse = data;
  const view = data.call;
  const status = view.call.status;
  modelBadge.textContent = data.model;
  renderJob(
    data.confirmed_job_facts,
    view.call.job_spec_version_id,
    connectedEstimatorSpec?.fields ?? {},
  );
  emptyState.hidden = true;
  transcript.hidden = false;
  callState.textContent = stateLabels[status] ?? status;
  liveIndicator.classList.toggle("active", !data.terminal);
  renderStages(status);
  renderTranscript(view.transcript);
  renderQuote(view.original_quote);
  renderOutcome(view.outcome);
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
    description.textContent = String(value);
    row.append(term, description);
    jobDetails.append(row);
  });
  jobEvidenceCount = Object.keys(evidencedFields).length;
  updateEvidenceCount();
}

function renderEvidenceSummary(evidencedFields) {
  const groups = new Map();
  Object.values(evidencedFields).forEach((evidence) => {
    const modality = evidence.source?.modality ?? "intake";
    const confidence = evidence.confidence ?? "confirmed";
    const label = `${modality.replaceAll("_", " ")} · ${confidence.replaceAll("_", " ")}`;
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

function renderStages(status) {
  const currentIndex = stateOrder.indexOf(status);
  const terminalState = ["complete", "callback_required", "declined", "incomplete"].includes(status);
  document.querySelectorAll("#stage-list li").forEach((item, index) => {
    item.classList.toggle("current", index === currentIndex);
    item.classList.toggle("complete", terminalState || (currentIndex >= 0 && index < currentIndex));
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
    facts.push({
      label: item.category,
      value: item.description,
      amount: item.amount == null ? null : `$${Number(item.amount).toFixed(2)}`,
    });
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
  evidenceCount.textContent = jobEvidenceCount
    ? `${jobEvidenceCount} job · ${quoteEvidenceCount} quote`
    : `${quoteEvidenceCount} ${quoteEvidenceCount === 1 ? "fact" : "facts"}`;
}

function renderOutcome(outcome) {
  outcomeCard.hidden = !outcome;
  if (!outcome) return;
  outcomeTitle.textContent = outcome.outcome_type.replaceAll("_", " ");
  const details = [outcome.reason, ...(outcome.validation_warnings ?? [])].filter(Boolean);
  outcomeDetail.textContent = details.join(" · ") || "Validated and stored with transcript evidence.";
}

async function loadConnectedEstimatorSpec() {
  if (!requestedJobSpecVersionId) return;
  try {
    connectedEstimatorSpec = await api(
      `/api/v1/intake/specs/${encodeURIComponent(requestedJobSpecVersionId)}`,
    );
    const facts = Object.fromEntries(
      Object.entries(connectedEstimatorSpec.fields).map(([name, evidence]) => [name, evidence.value]),
    );
    renderJob(facts, connectedEstimatorSpec.version_id, connectedEstimatorSpec.fields);
    callState.textContent = "Estimator evidence loaded";
  } catch (error) {
    showError(error.message);
  }
}

function showError(message) {
  callState.textContent = `Error: ${message}`;
  liveIndicator.classList.remove("active");
  recordingStatus.textContent = message;
}

if (requestedJobSpecVersionId) {
  jobVersion.textContent = "CONFIRMED JOB · ESTIMATOR CONNECTED";
  jobTitle.textContent = "Estimator specification ready";
}
loadConnectedEstimatorSpec();

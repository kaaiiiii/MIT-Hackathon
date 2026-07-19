const form = document.querySelector("#job-form");
const buildButton = document.querySelector("#build-button");
const resetButton = document.querySelector("#reset-button");
const confirmButton = document.querySelector("#confirm-button");
const notice = document.querySelector("#notice");
const reviewEmpty = document.querySelector("#review-empty");
const reviewContent = document.querySelector("#review-content");
const confirmedResult = document.querySelector("#confirmed-result");
const fieldList = document.querySelector("#field-list");
const missingFields = document.querySelector("#missing-fields");
const draftStatus = document.querySelector("#draft-status");
const voiceStartButton = document.querySelector("#voice-start-button");
const voiceStatus = document.querySelector("#voice-status");
const voiceHost = document.querySelector("#voice-host");
const researchPanel = document.querySelector("#research-panel");
const researchStatus = document.querySelector("#research-status");
const researchSummary = document.querySelector("#research-summary");
const researchDetails = document.querySelector("#research-details");

const fieldDefinitions = [
  ["service", "service", "Service"],
  ["origin.location", "origin-location", "Pickup"],
  ["destination.location", "destination-location", "Delivery"],
  ["requested_date", "requested-date", "Date"],
  ["item.type", "item-type", "Item"],
  ["item.dimensions", "item-dimensions", "Dimensions"],
  ["item.weight", "item-weight", "Weight"],
  ["origin.access", "origin-access", "Pickup access"],
  ["destination.access", "destination-access", "Delivery access"],
];

let sessionId = null;
let busy = false;
let voiceWidget = null;
let researchBusy = false;
let researchBundle = null;

async function api(path, options = {}) {
  const isForm = options.body instanceof FormData;
  const response = await fetch(path, {
    ...options,
    headers: isForm ? options.headers : {
      "content-type": "application/json",
      ...options.headers,
    },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body.detail === "string"
      ? body.detail
      : "The Estimator could not complete that step.";
    throw new Error(detail);
  }
  return body;
}

function evidenceDocument() {
  return {
    fields: fieldDefinitions.map(([fieldName, inputId], index) => ({
      field_name: fieldName,
      value: document.querySelector(`#${inputId}`).value.trim(),
      region: { form_field: inputId, line: index + 1 },
      confidence_score: 0.99,
    })),
  };
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy || !form.reportValidity()) return;
  setBusy(true);
  setNotice("Building the draft and attaching evidence...", "working");
  try {
    await ensureSession();

    const upload = new FormData();
    upload.append("document_type", "moving_inventory_json");
    upload.append(
      "document",
      new Blob([JSON.stringify(evidenceDocument())], { type: "application/json" }),
      "browser-intake.json",
    );
    const result = await api(`/api/v1/intake/sessions/${sessionId}/documents`, {
      method: "POST",
      body: upload,
    });
    renderDraft(result.session);
    setStep(2);
    await runWebSearch();
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setBusy(false);
  }
});

voiceStartButton.addEventListener("click", async () => {
  if (busy || voiceWidget) return;
  setBusy(true);
  setNotice("Preparing a private ElevenLabs Agents interview...", "working");
  try {
    await ensureSession();
    const result = await api(`/api/v1/intake/sessions/${sessionId}/voice`, {
      method: "POST",
    });
    renderDraft(result.session);
    if (result.voice_provider !== "elevenlabs_agents") {
      throw new Error(
        "ElevenLabs Agents is not configured. Set ELEVENLABS_API_KEY and ELEVENLABS_INTAKE_AGENT_ID, then restart the API."
      );
    }
    mountVoiceWidget(result);
    setNotice("Voice interview ready. Start the conversation in the ElevenLabs panel.", "success");
  } catch (error) {
    voiceStatus.textContent = error.message;
    voiceStatus.className = "voice-status error";
    setNotice(error.message, "error");
  } finally {
    setBusy(false);
  }
});

confirmButton.addEventListener("click", async () => {
  if (busy || !sessionId) return;
  setBusy(true);
  setNotice("Confirming and freezing the specification...", "working");
  try {
    const result = await api(`/api/v1/intake/sessions/${sessionId}/confirm`, {
      method: "POST",
      body: JSON.stringify({ approved: true, confirmed_by: "estimator_lab_user" }),
    });
    renderConfirmed(result);
    setStep(3);
    setNotice("Success. The Caller can now use this exact version.", "success");
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setBusy(false);
  }
});

resetButton.addEventListener("click", () => {
  sessionId = null;
  voiceWidget?.remove();
  voiceWidget = null;
  voiceHost.replaceChildren();
  voiceStatus.textContent = "Requires the ElevenLabs API key and intake Agent ID.";
  voiceStatus.className = "voice-status";
  researchBusy = false;
  researchBundle = null;
  researchPanel.hidden = true;
  researchSummary.textContent = "";
  researchDetails.replaceChildren();
  reviewEmpty.hidden = false;
  reviewContent.hidden = true;
  confirmedResult.hidden = true;
  fieldList.replaceChildren();
  draftStatus.textContent = "Not built";
  draftStatus.className = "pill muted";
  setStep(1);
  setNotice("Change any sample details, then build a new draft.");
});

async function ensureSession() {
  if (sessionId) return sessionId;
  const session = await api("/api/v1/intake/sessions", {
    method: "POST",
    body: JSON.stringify({ vertical: "moving", benchmark_refs: [] }),
  });
  sessionId = session.session_id;
  return sessionId;
}

function mountVoiceWidget(result) {
  voiceHost.replaceChildren();
  const widget = document.createElement("elevenlabs-convai");
  widget.setAttribute("signed-url", result.provider_connection_url);
  widget.setAttribute("variant", "expanded");
  widget.setAttribute("start-call-text", "Begin intake interview");
  widget.setAttribute("end-call-text", "End interview");
  widget.setAttribute("dynamic-variables", JSON.stringify(result.provider_context));
  widget.addEventListener("elevenlabs-convai:call", (event) => {
    event.detail.config.clientTools = {
      capture_intake_evidence: captureVoiceEvidence,
    };
  });
  voiceWidget = widget;
  voiceHost.append(widget);
  voiceStatus.textContent = "ElevenLabs Agents connected to this Estimator draft.";
  voiceStatus.className = "voice-status connected";
}

async function captureVoiceEvidence(parameters) {
  const payload = {
    turn_id: `voice_${crypto.randomUUID()}`,
    user_text: parameters.user_text,
    field_name: parameters.field_name ?? null,
    value: parameters.value ?? null,
    mark_unknown: parameters.mark_unknown ?? false,
    unknown_acknowledged: parameters.unknown_acknowledged ?? false,
  };
  const result = await api(`/api/v1/intake/sessions/${sessionId}/voice`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  renderDraft(result.session);
  if (!researchBundle && Object.keys(result.session.fields ?? {}).length > 0) {
    void runWebSearch();
  }
  if (result.session.status === "awaiting_confirmation") {
    setStep(2);
    setNotice("Voice interview complete. GPT is extending the job context now.", "working");
  }
  return {
    saved: true,
    status: result.session.status,
    next_field: result.next_field,
    question_objective: result.next_question_objective,
    spoken_question: result.spoken_question,
    missing_required_fields: result.session.missing_required_fields,
  };
}

async function runWebSearch() {
  if (!sessionId || researchBusy || researchBundle) return researchBundle;
  researchBusy = true;
  researchPanel.hidden = false;
  researchStatus.textContent = "Researching";
  researchStatus.className = "pill";
  researchSummary.textContent = "Checking current sources and building questions, risks, and terminology for the calls.";
  try {
    researchBundle = await api(`/api/v1/research/intake/sessions/${sessionId}/enrich`, {
      method: "POST",
    });
    renderResearch(researchBundle);
    setNotice("Evidence and cited GPT context are ready. Review them, then confirm.", "success");
    return researchBundle;
  } catch (error) {
    researchStatus.textContent = "Unavailable";
    researchStatus.className = "pill muted";
    researchSummary.textContent = error.message;
    setNotice(`The evidence draft is ready, but web search failed: ${error.message}`, "error");
    return null;
  } finally {
    researchBusy = false;
  }
}

function renderResearch(bundle) {
  const artifact = bundle.artifact;
  researchPanel.hidden = false;
  researchStatus.textContent = bundle.model;
  researchStatus.className = "pill ready";
  researchSummary.textContent = artifact.topic_summary;
  researchDetails.replaceChildren();

  const groups = [
    ["Risks to verify", artifact.risk_factors],
    ["Questions for vendors", artifact.suggested_vendor_questions],
    ["Research limitations", artifact.limitations],
  ];
  for (const [label, values] of groups) {
    if (!values?.length) continue;
    const section = document.createElement("section");
    const heading = document.createElement("strong");
    const list = document.createElement("ul");
    heading.textContent = label;
    for (const value of values) {
      const item = document.createElement("li");
      item.textContent = value;
      list.append(item);
    }
    section.append(heading, list);
    researchDetails.append(section);
  }
  if (artifact.sources?.length) {
    const section = document.createElement("section");
    const heading = document.createElement("strong");
    heading.textContent = "Sources";
    const list = document.createElement("ul");
    for (const source of artifact.sources) {
      const item = document.createElement("li");
      const link = document.createElement("a");
      link.href = source.url;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = source.title;
      item.append(link);
      list.append(item);
    }
    section.append(heading, list);
    researchDetails.append(section);
  }
}

function renderDraft(session) {
  reviewEmpty.hidden = true;
  confirmedResult.hidden = true;
  reviewContent.hidden = false;
  fieldList.replaceChildren();

  for (const [fieldName, , label] of fieldDefinitions) {
    const evidence = session.fields[fieldName];
    if (!evidence) continue;
    const row = document.createElement("article");
    const copy = document.createElement("div");
    const title = document.createElement("span");
    const value = document.createElement("strong");
    const source = document.createElement("small");
    title.textContent = label;
    value.textContent = String(evidence.value);
    source.textContent = `${evidence.source.modality} evidence - ${evidence.confidence}`;
    copy.append(title, value, source);
    const badge = document.createElement("b");
    badge.textContent = "Evidenced";
    row.append(copy, badge);
    fieldList.append(row);
  }

  const missing = session.missing_required_fields ?? [];
  missingFields.hidden = missing.length === 0;
  missingFields.textContent = missing.length
    ? `Still needed: ${missing.join(", ")}`
    : "";
  confirmButton.disabled = session.status !== "awaiting_confirmation";
  draftStatus.textContent = session.status === "awaiting_confirmation"
    ? "Ready to confirm"
    : session.status.replaceAll("_", " ");
  draftStatus.className = "pill ready";
}

function renderConfirmed(result) {
  reviewContent.hidden = true;
  confirmedResult.hidden = false;
  draftStatus.textContent = "Confirmed";
  draftStatus.className = "pill ready";
  document.querySelector("#version-id").textContent = result.version_id;
  document.querySelector("#canonical-hash").textContent = result.canonical_hash;
  document.querySelector("#continue-caller").href = (
    `/demo/?job_spec_version_id=${encodeURIComponent(result.version_id)}`
  );
}

function setBusy(value) {
  busy = value;
  buildButton.disabled = value;
  confirmButton.disabled = value;
  resetButton.disabled = value;
  voiceStartButton.disabled = value || Boolean(voiceWidget);
}

function setStep(step) {
  document.querySelectorAll(".steps li").forEach((item) => {
    const itemStep = Number(item.dataset.step);
    item.classList.toggle("active", itemStep === step);
    item.classList.toggle("complete", itemStep < step);
  });
}

function setNotice(message, kind = "") {
  notice.textContent = message;
  notice.className = `notice ${kind}`.trim();
}

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
    const session = await api("/api/v1/intake/sessions", {
      method: "POST",
      body: JSON.stringify({ vertical: "moving", benchmark_refs: [] }),
    });
    sessionId = session.session_id;

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
    setNotice("Draft built. Review the evidence, then confirm it.", "success");
  } catch (error) {
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
  reviewEmpty.hidden = false;
  reviewContent.hidden = true;
  confirmedResult.hidden = true;
  fieldList.replaceChildren();
  draftStatus.textContent = "Not built";
  draftStatus.className = "pill muted";
  setStep(1);
  setNotice("Change any sample details, then build a new draft.");
});

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

const quoteCount = document.querySelector("#quote-count");
const capNote = document.querySelector("#cap-note");
const startButton = document.querySelector("#start-session");
const setupPanel = document.querySelector("#setup-panel");
const collectionPanel = document.querySelector("#collection-panel");
const progressText = document.querySelector("#progress-text");
const sessionLabel = document.querySelector("#session-label");
const agencyName = document.querySelector("#agency-name");
const recordButton = document.querySelector("#sample-record");
const recordLabel = document.querySelector("#sample-record-label");
const sampleStatus = document.querySelector("#sample-status");
const synthesizeButton = document.querySelector("#synthesize");
const analysisButton = document.querySelector("#run-analysis");
const newSessionButton = document.querySelector("#new-session");
const sampleGrid = document.querySelector("#sample-grid");
const analysisPanel = document.querySelector("#analysis-panel");
const analysisModel = document.querySelector("#analysis-model");
const analysisSummary = document.querySelector("#analysis-summary");
const analysisLowest = document.querySelector("#analysis-lowest");
const analysisObservations = document.querySelector("#analysis-observations");
const analysisQuotes = document.querySelector("#analysis-quotes");

const MAX_WEBSITE_QUOTES = 20;
const CAP_MESSAGE =
  `We can prepare up to ${MAX_WEBSITE_QUOTES} sample quotes from the website. ` +
  "For more than that, please contact support.";

let sessionId = null;
let session = null;
let busy = false;
let mediaRecorder = null;
let microphoneStream = null;
let audioChunks = [];

quoteCount.addEventListener("input", () => {
  const value = Number(quoteCount.value);
  if (value > MAX_WEBSITE_QUOTES) {
    capNote.textContent = CAP_MESSAGE;
    capNote.classList.add("warn");
  } else {
    capNote.textContent = "Default is 5 quotes. You can request up to 20 from the website.";
    capNote.classList.remove("warn");
  }
});

async function api(path, options = {}) {
  const headers = options.body instanceof FormData
    ? { ...options.headers }
    : { "content-type": "application/json", ...options.headers };
  const response = await fetch(path, { ...options, headers });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail ?? `Request failed (${response.status})`);
  return body;
}

startButton.addEventListener("click", async () => {
  if (busy) return;
  const requested = Number(quoteCount.value) || 5;
  if (requested > MAX_WEBSITE_QUOTES) {
    capNote.textContent = CAP_MESSAGE;
    capNote.classList.add("warn");
    return;
  }
  setBusy(true);
  try {
    session = await api("/api/v1/samples/sessions", {
      method: "POST",
      body: JSON.stringify({ target_quotes: requested }),
    });
    sessionId = session.session_id;
    setupPanel.classList.add("hidden");
    collectionPanel.classList.remove("hidden");
    sessionLabel.textContent = sessionId.slice(0, 16);
    render();
  } catch (error) {
    capNote.textContent = error.message;
    capNote.classList.add("warn");
  } finally {
    setBusy(false);
  }
});

newSessionButton.addEventListener("click", () => {
  sessionId = null;
  session = null;
  sampleGrid.replaceChildren();
  collectionPanel.classList.add("hidden");
  setupPanel.classList.remove("hidden");
});

recordButton.addEventListener("click", async () => {
  if (!sessionId || busy) return;
  if (mediaRecorder?.state === "recording") {
    mediaRecorder.stop();
    recordButton.classList.remove("recording");
    recordLabel.textContent = "Record sample call";
    setStatus("Transcribing and saving the sample…");
    return;
  }
  if (session && session.remaining_slots <= 0) {
    setStatus("All slots are filled. Start over to collect a new set.", true);
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
    mediaRecorder.start(250);
    recordButton.classList.add("recording");
    recordLabel.textContent = "Stop and save";
    setStatus("Recording… speak as the agency, then press stop.");
  } catch (error) {
    setStatus(`Microphone unavailable: ${error.message}`, true);
  }
});

async function submitRecording() {
  const mimeType = mediaRecorder?.mimeType || "audio/webm";
  const blob = new Blob(audioChunks, { type: mimeType });
  microphoneStream?.getTracks().forEach((track) => track.stop());
  microphoneStream = null;
  mediaRecorder = null;
  if (!blob.size) {
    setStatus("The recording was empty.", true);
    return;
  }
  setBusy(true);
  try {
    const formData = new FormData();
    formData.append("audio", blob, mimeType.includes("webm") ? "sample.webm" : "sample-audio");
    const name = agencyName.value.trim();
    if (name) formData.append("agency_name", name);
    session = await api(`/api/v1/samples/sessions/${sessionId}/recordings`, {
      method: "POST",
      body: formData,
    });
    agencyName.value = "";
    setStatus("Saved. Record another, or generate the rest synthetically.");
    render();
    await maybeAutoAnalyze();
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    setBusy(false);
  }
}

synthesizeButton.addEventListener("click", async () => {
  if (!sessionId || busy) return;
  setBusy(true);
  setStatus("Generating the remaining quotes with GPT and voicing them…");
  try {
    session = await api(`/api/v1/samples/sessions/${sessionId}/synthesize`, {
      method: "POST",
    });
    setStatus("Synthetic quotes ready.");
    render();
    await maybeAutoAnalyze();
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    setBusy(false);
  }
});

analysisButton.addEventListener("click", () => runAnalysis());

async function runAnalysis() {
  if (!sessionId || busy) return;
  setBusy(true);
  setStatus("Analyzing the collected quotes with GPT…");
  try {
    session = await api(`/api/v1/samples/sessions/${sessionId}/analyze`, {
      method: "POST",
    });
    setStatus("Analysis ready below.");
    render();
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    setBusy(false);
  }
}

// The flow ends with the analysis: run it automatically once every slot is filled.
async function maybeAutoAnalyze() {
  if (session && session.remaining_slots === 0 && !session.analysis) {
    setBusy(false);
    await runAnalysis();
  }
}

function render() {
  if (!session) return;
  const total = session.recorded_count + session.synthetic_count;
  progressText.textContent =
    `${total} of ${session.target_quotes} collected · ` +
    `${session.recorded_count} recorded, ${session.synthetic_count} synthetic`;
  synthesizeButton.disabled =
    session.recorded_count === 0 || session.remaining_slots === 0;
  analysisButton.disabled = total === 0;
  renderAnalysis(session.analysis);
  sampleGrid.replaceChildren();
  session.recordings.forEach((recording, index) => {
    const card = document.createElement("article");
    card.className = "sample-card";
    const header = document.createElement("header");
    const title = document.createElement("strong");
    title.textContent = recording.agency_name || `Sample ${index + 1}`;
    const tag = document.createElement("span");
    tag.className = `tag ${recording.source}`;
    tag.textContent = recording.source === "recorded" ? "RECORDED" : "SYNTHETIC";
    header.append(title, tag);
    card.append(header);
    if (recording.quote_total != null) {
      const total = document.createElement("strong");
      total.textContent = `Quoted total: $${Number(recording.quote_total).toFixed(2)}`;
      card.append(total);
    }
    const transcript = document.createElement("p");
    transcript.textContent = recording.transcript;
    card.append(transcript);
    if (recording.has_audio) {
      const audio = document.createElement("audio");
      audio.controls = true;
      audio.preload = "none";
      audio.src = `/api/v1/samples/sessions/${sessionId}/recordings/${recording.recording_id}/audio`;
      card.append(audio);
    }
    sampleGrid.append(card);
  });
}

function renderAnalysis(analysis) {
  analysisPanel.classList.toggle("hidden", !analysis);
  if (!analysis) return;
  analysisModel.textContent = "GPT analysis";
  analysisSummary.textContent = analysis.summary;
  analysisLowest.textContent = analysis.lowest_quote_agency
    ? `Lowest stated total: ${analysis.lowest_quote_agency}`
    : "No stated totals to compare yet.";
  analysisObservations.replaceChildren();
  (analysis.observations ?? []).forEach((note) => {
    const item = document.createElement("li");
    item.textContent = note;
    analysisObservations.append(item);
  });
  analysisQuotes.replaceChildren();
  (analysis.quotes ?? []).forEach((quote) => {
    const card = document.createElement("article");
    card.className = "sample-card";
    const title = document.createElement("strong");
    title.textContent = quote.agency_name;
    card.append(title);
    const rows = [
      ["Total", quote.quote_total != null ? `$${Number(quote.quote_total).toFixed(2)}` : null],
      ["Pricing", quote.pricing_model],
      ["Fees", quote.fees],
      ["Binding", quote.binding_status],
      ["Availability", quote.availability],
    ];
    rows.forEach(([label, value]) => {
      if (!value) return;
      const line = document.createElement("p");
      line.textContent = `${label}: ${value}`;
      card.append(line);
    });
    (quote.red_flags ?? []).forEach((flag) => {
      const warn = document.createElement("p");
      warn.textContent = `⚠ ${flag}`;
      card.append(warn);
    });
    analysisQuotes.append(card);
  });
}

function setStatus(message, isError = false) {
  sampleStatus.textContent = message;
  sampleStatus.classList.toggle("error", isError);
}

function setBusy(value) {
  busy = value;
  startButton.disabled = value;
  recordButton.disabled = value && mediaRecorder?.state !== "recording";
  synthesizeButton.disabled =
    value || !session || session.recorded_count === 0 || session.remaining_slots === 0;
  analysisButton.disabled =
    value || !session || session.recorded_count + session.synthetic_count === 0;
}

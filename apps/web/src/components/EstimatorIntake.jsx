import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  captureVoiceEvidence,
  confirmIntake,
  createIntakeSession,
  enrichIntake,
  importElevenLabsConversation,
  startVoiceIntake,
} from '../lib/api';
import { useReport } from '../lib/ReportContext';

const REQUIRED_ELEVENLABS_VARIABLES = [
  'disclosure',
  'next_field',
  'question_objective',
  'spoken_question',
];

function validateVoiceConnection(connection) {
  if (connection.voice_provider !== 'elevenlabs_agents') {
    throw new Error('ElevenLabs Agents is not configured on the API server.');
  }
  const missing = REQUIRED_ELEVENLABS_VARIABLES.filter((name) => {
    const value = connection.provider_context?.[name];
    return typeof value !== 'string' || !value.trim();
  });
  if (missing.length) {
    throw new Error(`The API did not supply required ElevenLabs dynamic variables: ${missing.join(', ')}.`);
  }
  return connection;
}

function EvidenceList({ fields }) {
  const entries = Object.entries(fields ?? {});
  if (!entries.length) return null;
  return (
    <div className="intake-evidence">
      {entries.map(([name, field]) => (
        <div className="intake-evidence__row" key={name}>
          <span className="mono">{name}</span>
          <strong>{String(field.value)}</strong>
          <small>{field.source?.modality} · {field.confidence}</small>
        </div>
      ))}
    </div>
  );
}
function ResearchPreview({ bundle }) {
  if (!bundle) return null;
  const artifact = bundle.artifact;
  return (
    <div className="intake-research">
      <p className="micro intake-research__label">GPT CONTEXT · {bundle.model}</p>
      <p>{artifact.topic_summary}</p>
      {!!artifact.suggested_vendor_questions?.length && (
        <ul>
          {artifact.suggested_vendor_questions.slice(0, 3).map((question) => (
            <li key={question}>{question}</li>
          ))}
        </ul>
      )}
      {!!artifact.sources?.length && (
        <p className="micro muted">{artifact.sources.length} cited source(s) stored for later calls.</p>
      )}
    </div>
  );
}

export default function EstimatorIntake() {
  const [session, setSession] = useState(null);
  const [voice, setVoice] = useState(null);
  const [research, setResearch] = useState(null);
  const [confirmed, setConfirmed] = useState(null);
  const [status, setStatus] = useState('Tell us about the job once. We will preserve the evidence for every vendor call.');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [voiceEnded, setVoiceEnded] = useState(false);
  const [conversationId, setConversationId] = useState('');
  const [importSummary, setImportSummary] = useState(null);
  const widgetHost = useRef(null);
  const conversationStartedAt = useRef(null);
  const researchStarted = useRef(false);
  const { loadBackendReport } = useReport();

  const confirmMicrophoneAccess = async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error('This browser does not provide microphone access. Use a current Chrome, Edge, or Safari browser.');
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      if (err?.name === 'NotAllowedError') {
        throw new Error('Microphone permission was denied. Allow microphone access in the browser address bar, then try again.');
      }
      throw new Error(`The microphone could not be opened: ${err?.message ?? 'unknown browser error'}`);
    } finally {
      stream?.getTracks().forEach((track) => track.stop());
    }
  };

  const runResearch = useCallback(async (sessionId) => {
    if (researchStarted.current) return;
    researchStarted.current = true;
    setStatus('Intake complete. Luna is researching risks and better vendor questions…');
    try {
      const bundle = await enrichIntake(sessionId);
      setResearch(bundle);
      setStatus('Evidence and cited research are ready for your confirmation.');
    } catch (err) {
      researchStarted.current = false;
      setError(`Research could not finish: ${err.message}`);
      setStatus('Your evidence is still safe. You can retry by ending and restarting intake.');
    }
  }, []);

  useEffect(() => {
    if (!voice?.provider_connection_url || !widgetHost.current) return undefined;
    const host = widgetHost.current;
    host.replaceChildren();
    const widget = document.createElement('elevenlabs-convai');
    widget.setAttribute('signed-url', voice.provider_connection_url);
    widget.setAttribute('variant', 'expanded');
    widget.setAttribute('start-call-text', 'Begin vocal description');
    widget.setAttribute('end-call-text', 'Finish intake');
    widget.setAttribute('dynamic-variables', JSON.stringify(voice.provider_context ?? {}));
    const handleCall = (event) => {
      setError(null);
      setStatus('ElevenLabs is opening the live voice conversation…');
      event.detail.config.clientTools = {
        capture_intake_evidence: async (parameters) => {
          const result = await captureVoiceEvidence(voice.provider_context.intake_session_id, {
            turn_id: `voice_${crypto.randomUUID()}`,
            user_text: parameters.user_text,
            field_name: parameters.field_name ?? null,
            value: parameters.value ?? null,
            mark_unknown: parameters.mark_unknown ?? false,
            unknown_acknowledged: parameters.unknown_acknowledged ?? false,
          });
          setSession(result.session);
          if (result.session.status === 'awaiting_confirmation') {
            void runResearch(result.session.session_id);
          }
          return {
            saved: true,
            status: result.session.status,
            next_field: result.next_field,
            question_objective: result.next_question_objective,
            spoken_question: result.spoken_question,
            missing_required_fields: result.session.missing_required_fields,
          };
        },
      };
    };
    const handleConversationStarted = (event) => {
      conversationStartedAt.current = performance.now();
      const detail = event?.detail;
      const detectedConversationId = detail?.conversationId ?? detail?.conversation_id;
      if (typeof detectedConversationId === 'string') {
        setConversationId(detectedConversationId);
      }
      setVoiceEnded(false);
      setError(null);
      setStatus('Live voice interview connected. Speak naturally.');
    };
    const handleConversationEnded = (event) => {
      const elapsedSeconds = conversationStartedAt.current == null
        ? null
        : (performance.now() - conversationStartedAt.current) / 1000;
      conversationStartedAt.current = null;
      setVoiceEnded(true);
      const detail = event?.detail;
      const reason = detail?.reason ?? detail?.terminationReason ?? detail?.termination_reason;
      if (elapsedSeconds != null && elapsedSeconds < 5) {
        const suffix = reason ? ` Reason: ${reason}.` : '';
        setError(`ElevenLabs ended the conversation after ${elapsedSeconds.toFixed(1)} seconds.${suffix} Check the Agent conversation log for an End Call or guardrail event.`);
        setStatus('The voice session ended before intake began. You can reconnect with a fresh signed URL.');
        return;
      }
      setStatus(reason ? `Voice conversation ended: ${reason}.` : 'Voice conversation ended.');
    };
    const handleConversationError = (event) => {
      const detail = event?.detail;
      const message = detail?.message ?? detail?.error?.message ?? 'The ElevenLabs widget reported a connection error.';
      setVoiceEnded(true);
      setError(String(message));
      setStatus('The live voice session failed. You can reconnect with a fresh signed URL.');
    };
    widget.addEventListener('elevenlabs-convai:call', handleCall);
    widget.addEventListener('conversationStarted', handleConversationStarted);
    widget.addEventListener('conversationEnded', handleConversationEnded);
    widget.addEventListener('error', handleConversationError);
    host.append(widget);
    return () => {
      widget.removeEventListener('elevenlabs-convai:call', handleCall);
      widget.removeEventListener('conversationStarted', handleConversationStarted);
      widget.removeEventListener('conversationEnded', handleConversationEnded);
      widget.removeEventListener('error', handleConversationError);
      widget.remove();
    };
  }, [voice, runResearch]);

  const start = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    setStatus('Checking microphone permission…');
    try {
      await confirmMicrophoneAccess();
      setStatus('Microphone confirmed. Opening a private ElevenLabs intake session…');
      const draft = await createIntakeSession();
      setSession(draft);
      const connection = validateVoiceConnection(await startVoiceIntake(draft.session_id));
      setVoiceEnded(false);
      setVoice(connection);
      setStatus('Microphone confirmed. Voice interview ready—start speaking in the panel below.');
    } catch (err) {
      setError(err.message);
      setStatus('The intake session could not start.');
    } finally {
      setBusy(false);
    }
  };

  const reconnectVoice = async () => {
    if (!session || busy) return;
    setBusy(true);
    setError(null);
    setStatus('Requesting a fresh ElevenLabs signed URL…');
    try {
      await confirmMicrophoneAccess();
      const connection = validateVoiceConnection(await startVoiceIntake(session.session_id));
      setVoiceEnded(false);
      setVoice(connection);
      setStatus('Fresh voice session ready. Select Begin vocal description.');
    } catch (err) {
      setError(err.message);
      setStatus('The voice session could not reconnect.');
    } finally {
      setBusy(false);
    }
  };

  const recoverConversation = async () => {
    const normalizedId = conversationId.trim();
    if (!session || busy || !normalizedId) return;
    setBusy(true);
    setError(null);
    setStatus('Fetching the completed ElevenLabs transcript and extracting evidence…');
    try {
      const result = await importElevenLabsConversation(session.session_id, normalizedId);
      setSession(result.session);
      setImportSummary(result);
      setStatus(`Recovered ${result.imported_fields.length} evidence field(s) from the ElevenLabs transcript. Review them before confirmation.`);
      if (result.session.status === 'awaiting_confirmation') {
        void runResearch(result.session.session_id);
      }
    } catch (err) {
      setError(err.message);
      setStatus('The ElevenLabs transcript could not be recovered.');
    } finally {
      setBusy(false);
    }
  };

  const downloadConfirmedJson = () => {
    if (!confirmed) return;
    const blob = new Blob([JSON.stringify(confirmed, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${confirmed.version_id}-caller-context.json`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const confirm = async () => {
    if (!session || busy) return;
    setBusy(true);
    setError(null);
    setStatus('Freezing the confirmed specification…');
    try {
      const result = await confirmIntake(session.session_id);
      setConfirmed(result);
      localStorage.setItem('nego_job_spec_version_id', result.version_id);
      await loadBackendReport(result.version_id);
      setStatus('Confirmed. Every vendor call will use this exact specification.');
    } catch (err) {
      setError(err.message);
      setStatus('Confirmation failed. Your draft has not been lost.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="call-slot" aria-labelledby="call-slot-title">
      <h2 id="call-slot-title" className="micro call-slot__kicker">YOUR VOCAL INTAKE</h2>
      {!voice && !confirmed && (
        <button type="button" className="call-slot__cta" onClick={start} disabled={busy}>
          <span className="call-slot__cta-dot" aria-hidden="true">●</span>{' '}
          {busy ? 'Connecting…' : 'Start vocal description'}
        </button>
      )}
      <p className="call-slot__note" role="status">{status}</p>
      {error && <p className="intake-error micro">{error}</p>}
      <div className="intake-widget" ref={widgetHost} />
      {voiceEnded && session && !confirmed && (
        <button type="button" className="btn" onClick={reconnectVoice} disabled={busy}>
          {busy ? 'Reconnecting…' : 'Reconnect voice interview'}
        </button>
      )}
      {session && !confirmed && (
        <div className="intake-recovery">
          <label className="micro" htmlFor="elevenlabs-conversation-id">
            Post-call fallback · ElevenLabs conversation ID
          </label>
          <input
            id="elevenlabs-conversation-id"
            type="text"
            value={conversationId}
            onChange={(event) => setConversationId(event.target.value)}
            placeholder="conv_…"
            spellCheck="false"
          />
          <button
            type="button"
            className="btn"
            onClick={recoverConversation}
            disabled={busy || !conversationId.trim()}
          >
            {busy ? 'Recovering…' : 'Recover transcript into Estimator'}
          </button>
        </div>
      )}
      {importSummary && (
        <p className="micro muted">
          Imported from {importSummary.conversation_id}: {importSummary.imported_fields.join(', ') || 'no supported fields'}.
        </p>
      )}
      <EvidenceList fields={session?.fields} />
      {!!session?.missing_required_fields?.length && (
        <p className="micro muted">Still needed: {session.missing_required_fields.join(', ')}</p>
      )}
      <ResearchPreview bundle={research} />
      {session?.status === 'awaiting_confirmation' && !confirmed && (
        <button type="button" className="btn intake-confirm" onClick={confirm} disabled={busy}>
          Confirm this specification
        </button>
      )}
      {confirmed && (
        <div className="intake-confirmed">
          <p><strong>Specification confirmed.</strong> <span className="mono">{confirmed.version_id}</span></p>
          <div className="intake-confirmed__actions">
            <a className="btn" href={`/demo/?job_spec_version_id=${encodeURIComponent(confirmed.version_id)}`}>
              Open Caller Lab →
            </a>
            <Link className="btn btn--small" to={`/report?spec=${encodeURIComponent(confirmed.version_id)}`}>
              View live report
            </Link>
            <button type="button" className="btn btn--small" onClick={downloadConfirmedJson}>
              Download Caller context JSON
            </button>
          </div>
        </div>
      )}
      {!session && (
        <Link to="/call/call_p1" className="mono call-slot__link">Read a sample call →</Link>
      )}
    </section>
  );
}

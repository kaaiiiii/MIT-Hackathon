import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  captureVoiceEvidence,
  confirmIntake,
  createIntakeSession,
  enrichIntake,
  startVoiceIntake,
} from '../lib/api';
import { useReport } from '../lib/ReportContext';

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
  const widgetHost = useRef(null);
  const researchStarted = useRef(false);
  const { loadBackendReport } = useReport();

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
    widget.addEventListener('elevenlabs-convai:call', (event) => {
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
    });
    host.append(widget);
    return () => widget.remove();
  }, [voice, runResearch]);

  const start = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    setStatus('Opening a private ElevenLabs intake session…');
    try {
      const draft = await createIntakeSession();
      setSession(draft);
      const connection = await startVoiceIntake(draft.session_id);
      if (connection.voice_provider !== 'elevenlabs_agents') {
        throw new Error('ElevenLabs Agents is not configured on the API server.');
      }
      setVoice(connection);
      setStatus('Voice interview ready. Start speaking in the panel below.');
    } catch (err) {
      setError(err.message);
      setStatus('The intake session could not start.');
    } finally {
      setBusy(false);
    }
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
          </div>
        </div>
      )}
      {!session && (
        <Link to="/call/call_p1" className="mono call-slot__link">Read a sample call →</Link>
      )}
    </section>
  );
}

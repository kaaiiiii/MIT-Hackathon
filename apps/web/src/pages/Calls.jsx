import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../lib/api';
import './calls.css';

// Step 2 of the demo: simulate the vendor calls for a confirmed specification.
// The working call console (state machine, evidence board, voice loop) lives at
// /demo/; this tab hosts it against the chosen spec so the whole flow stays in
// the app: Estimator -> Calls -> Report.
export default function Calls() {
  const [params, setParams] = useSearchParams();
  const [specs, setSpecs] = useState([]);
  const [error, setError] = useState('');
  const requested = params.get('spec');

  useEffect(() => {
    let cancelled = false;
    api('/api/v1/intake/specs')
      .then((rows) => {
        if (cancelled) return;
        setSpecs(rows.filter((row) => !row.superseded_by));
      })
      .catch((err) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, []);

  const active = requested ?? specs[0]?.version_id ?? null;
  const consoleUrl = useMemo(
    () => active ? `/demo/?job_spec_version_id=${encodeURIComponent(active)}` : null,
    [active],
  );

  return (
    <main className="calls-page">
      <header className="calls-page__head">
        <div>
          <p className="micro muted">STEP 2 · SIMULATE THE CALLS</p>
          <h1>Call the agencies</h1>
          <p className="muted">
            The AI Caller phones each agency with the confirmed job. Play the
            agency here — speak or type its side — and every quote fact lands on
            the evidence board with transcript proof.
          </p>
        </div>
        <div className="calls-page__controls">
          <label htmlFor="spec-select" className="micro muted">
            Confirmed specification
          </label>
          <select
            id="spec-select"
            value={active ?? ''}
            onChange={(event) => setParams({ spec: event.target.value })}
            disabled={!active}
          >
            {!active && <option value="">No confirmed specification</option>}
            {active && !specs.some((row) => row.version_id === active) && (
              <option value={active}>
                {active}
              </option>
            )}
            {specs.map((row) => (
              <option key={row.version_id} value={row.version_id}>
                {row.version_id} · {new Date(row.confirmed_at).toLocaleString()}
              </option>
            ))}
          </select>
          {active && (
            <Link className="mono calls-page__report" to={`/report?spec=${encodeURIComponent(active)}`}>
              Continue to the report →
            </Link>
          )}
        </div>
      </header>
      {error && <p className="calls-page__error">{error}</p>}
      {consoleUrl ? (
        <iframe
          key={consoleUrl}
          className="calls-page__console"
          src={consoleUrl}
          title="Call simulation console"
          allow="microphone; autoplay"
        />
      ) : (
        <section className="calls-page__empty">
          <h2>No confirmed job yet</h2>
          <p>The Caller will not substitute a sample job. Complete and confirm the Estimator intake first.</p>
          <Link className="btn" to="/">Return to Estimator</Link>
        </section>
      )}
    </main>
  );
}

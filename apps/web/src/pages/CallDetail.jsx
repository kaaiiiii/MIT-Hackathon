import { useMemo, useState } from 'react';
import { Link, Navigate, NavLink, useParams } from 'react-router-dom';
import { useReport } from '../lib/ReportContext';
import {
  bindingWords,
  evidenceEventIds,
  heardAt,
  mmss,
  money,
  momentNotes,
  moveSentence,
  quoteTotal,
  OUTCOME_LABEL,
} from '../lib/report';
import PriceChart from '../components/PriceChart';
import './call.css';

function Banner({ call, isPick }) {
  const outcome = call.outcome ?? {};
  const otype = outcome.outcome_type;
  const binding = (call.quote?.binding_status || '').toLowerCase() === 'binding';
  let text;
  if (otype === 'complete_quote') {
    text = `Firm quote in hand — ${binding ? 'price locked in writing.' : 'price could change on moving day.'}`;
  } else if (otype === 'callback_required') {
    text = outcome.reason ?? 'They promised to call back with a firm number.';
  } else if (otype === 'declined') {
    text = outcome.reason ?? 'They could not take the job.';
  } else {
    text = outcome.reason ?? 'Call in progress.';
  }
  return (
    <p className={`call-banner mono${isPick ? ' call-banner--pick' : ''}`}>
      <span className="micro muted call-banner__label">{OUTCOME_LABEL[otype] ?? 'IN PROGRESS'}</span>{' '}
      {text}
    </p>
  );
}

function Transcript({ call }) {
  const [mode, setMode] = useState('full');
  const cited = useMemo(() => evidenceEventIds(call), [call]);
  const notes = useMemo(() => momentNotes(call), [call]);
  const events = call.transcript ?? [];

  const isKey = (e) => notes.has(e.event_id);
  const shown = mode === 'key' ? events.filter(isKey) : events;

  // Deposition-style elision markers between key moments.
  const rows = [];
  if (mode === 'key') {
    let cursor = 0;
    for (const e of events) {
      if (!isKey(e)) continue;
      const skipped = events.indexOf(e) - cursor;
      if (skipped > 0) {
        rows.push({ omitted: skipped, key: `om-${e.event_id}` });
      }
      rows.push({ event: e, key: e.event_id });
      cursor = events.indexOf(e) + 1;
    }
    const tail = events.length - cursor;
    if (rows.length && tail > 0) rows.push({ omitted: tail, key: 'om-tail' });
  } else {
    for (const e of events) rows.push({ event: e, key: e.event_id ?? Math.random() });
  }

  return (
    <div className="convo">
      <div className="convo__head">
        <h2 className="micro muted">The conversation</h2>
        <div className="convo__modes" role="group" aria-label="Transcript view">
          <button
            type="button"
            className={`convo__mode${mode === 'full' ? ' is-on' : ''}`}
            onClick={() => setMode('full')}
            aria-pressed={mode === 'full'}
          >
            Full
          </button>
          <button
            type="button"
            className={`convo__mode${mode === 'key' ? ' is-on' : ''}`}
            onClick={() => setMode('key')}
            aria-pressed={mode === 'key'}
          >
            Key moments
          </button>
        </div>
      </div>

      {call.recording_url && (
        // eslint-disable-next-line jsx-a11y/media-has-caption
        <audio controls src={call.recording_url} className="convo__audio" />
      )}

      {mode === 'key' && !shown.length && (
        <p className="mono muted">No cited moments on this call — read the full conversation.</p>
      )}

      {rows.map((row) =>
        row.omitted ? (
          <p key={row.key} className="convo__omitted micro faint">
            — {row.omitted} line{row.omitted !== 1 ? 's' : ''} omitted —
          </p>
        ) : (
          <TranscriptTurn
            key={row.key}
            event={row.event}
            note={notes.get(row.event.event_id)}
            cited={cited.has(row.event.event_id)}
          />
        )
      )}
    </div>
  );
}

function TranscriptTurn({ event, note, cited }) {
  const speaker = event.speaker ?? 'system';
  return (
    <div className={`turn turn--${speaker}`}>
      <span className="turn__stamp">{mmss(event.timestamp_seconds) ?? '—'}</span>
      <div className="turn__body">
        <span className="micro muted turn__who">
          {speaker === 'agent' ? 'Agent (AI)' : speaker}
        </span>
        {note && <p className="turn__note delta delta--down">{note}</p>}
        {!note && cited && <p className="turn__cited micro muted">Quoted in the report</p>}
        <p className="turn__text">{event.text ?? ''}</p>
      </div>
    </div>
  );
}

function Receipts({ call }) {
  const quote = call.quote ?? {};
  const evidence = quote.evidence ?? {};
  const facts = [
    ['Pricing model', quote.pricing_model, evidence.pricing_model],
    [
      'Estimated total',
      quote.estimated_total != null ? money(quote.estimated_total, 2) : null,
      evidence.estimated_total,
    ],
    ['Binding status', quote.binding_status, evidence.binding_status],
    ['Deposit', quote.deposit, evidence.deposit],
    ['Fees', quote.fees, evidence.fees],
    ['Availability', quote.availability, evidence.availability],
  ].filter(([, v]) => v != null && v !== '');
  const items = quote.line_items ?? [];

  return (
    <details className="fold" open>
      <summary>the receipts — what we took from this call</summary>
      <div className="fold-body">
        {facts.length ? (
          <table className="ledger">
            <thead>
              <tr>
                <th>Fact</th>
                <th>Value</th>
                <th>Heard at</th>
              </tr>
            </thead>
            <tbody>
              {facts.map(([f, v, e]) => (
                <tr key={f}>
                  <td>{f}</td>
                  <td>{String(v)}</td>
                  <td className="faint">{heardAt(call, e) ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="mono muted">No extracted quote facts on this call.</p>
        )}

        {items.length > 0 && (
          <>
            <p className="micro muted receipts__sub">Itemized costs</p>
            <table className="ledger">
              <tbody>
                {items.map((item, i) => (
                  <tr key={i}>
                    <td>{item.category}</td>
                    <td>{item.description}</td>
                    <td className="num">{money(item.amount)}</td>
                    <td className="faint">{heardAt(call, item.evidence_event_id) ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}

        {(quote.killed_fees ?? []).map((fee, i) => {
          const at = heardAt(call, fee.evidence_event_id);
          return (
            <p key={i} className="mono receipts__killed">
              <span className="strike">{money(fee.original_amount)}</span>{' '}
              <strong>{fee.category}</strong> waived
              {at && <span className="faint"> — heard at {at}</span>}
            </p>
          );
        })}
        {(quote.assumptions ?? []).map((note, i) => (
          <p key={i} className="micro muted">
            Assumption: {note}
          </p>
        ))}
      </div>
    </details>
  );
}

export default function CallDetail() {
  const { id } = useParams();
  const { report } = useReport();

  if (!report) return null;
  const calls = report.calls ?? [];
  const call = calls.find((c) => c.call_id === id);
  if (!call) return <Navigate to="/report" replace />;

  const vendor = call.vendor ?? {};
  const quote = call.quote ?? {};
  const recCallId = report.recommendation?.call_id ?? null;
  const isPick = call.call_id === recCallId;

  return (
    <main className="call">
      <div className="call__topbar">
        <Link to="/report" className="btn btn--small">
          ← All calls
        </Link>
        <nav className="call__switcher" aria-label="Seller">
          {calls.map((c) => (
            <NavLink
              key={c.call_id}
              to={`/call/${c.call_id}`}
              className={({ isActive }) => `call__switch${isActive ? ' is-on' : ''}`}
            >
              {(c.vendor?.name ?? c.call_id).split(' ')[0]}
            </NavLink>
          ))}
        </nav>
      </div>
      <p className="micro faint call__context">Your move: {moveSentence(report)}</p>

      <header className="call__head">
        <h1 className="call__name">{vendor.name ?? 'Unknown vendor'}</h1>
        <p className="mono muted call__meta">
          {[vendor.phone, typeof vendor.rating === 'number' ? `rated ${vendor.rating.toFixed(1)}` : null, vendor.negotiation_style]
            .filter(Boolean)
            .join(' · ')}
        </p>
      </header>

      <Banner call={call} isPick={isPick} />

      <dl className="call__metrics">
        <div>
          <dt className="micro muted">Outcome</dt>
          <dd className="mono">{OUTCOME_LABEL[call.outcome?.outcome_type] ?? 'IN PROGRESS'}</dd>
        </div>
        <div>
          <dt className="micro muted">Final price</dt>
          <dd className="mono">{money(quoteTotal(call))}</dd>
        </div>
        <div>
          <dt className="micro muted">Price locked?</dt>
          <dd className="mono">{bindingWords(quote)}</dd>
        </div>
      </dl>

      {(quote.negotiation_moves ?? []).length > 0 && (
        <section aria-label="How the price moved" className="call__chart">
          <h2 className="micro muted call__chart-title">How the price moved</h2>
          <PriceChart call={call} isPick={isPick} />
        </section>
      )}

      <div className="call__cols">
        <Transcript call={call} />
        <aside className="call__side">
          <Receipts call={call} />
          {(quote.red_flags ?? []).map((flag, i) => {
            const at = heardAt(call, flag.evidence_event_id);
            return (
              <p key={i} className="call__flag mono">
                {flag.detail ?? ''}
                {at && <span className="faint"> — heard at {at}</span>}
              </p>
            );
          })}
        </aside>
      </div>
    </main>
  );
}

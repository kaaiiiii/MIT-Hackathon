import { Link } from 'react-router-dom';
import { useReport } from '../lib/ReportContext';
import {
  decisionContext,
  feeMatrix,
  firstClause,
  heardAt,
  humanDate,
  mmss,
  money,
  moveSentence,
  outcomeCounts,
  transcriptIndex,
} from '../lib/report';
import CallsBlotter from '../components/CallsBlotter';
import MarketStrip from '../components/MarketStrip';
import './report.css';

// Digits wear mono everywhere — including inside display-font prose.
function MonoDigits({ text }) {
  const parts = String(text).split(/(\$?[\d][\d,.]*)/g);
  return parts.map((p, i) =>
    /^\$?[\d]/.test(p) ? (
      <span key={i} className="mono-digit">
        {p}
      </span>
    ) : (
      <span key={i}>{p}</span>
    )
  );
}

function SectionHead({ no, title, headId }) {
  return (
    <div className="section-head">
      <span className="no">{no}</span>
      <h2 id={headId}>{title}</h2>
    </div>
  );
}

const COUNT_WORDS = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve'];

function DataDemoCluster() {
  const {
    source,
    uploadName,
    uploadError,
    backendError,
    backendVersionId,
    refreshBackendReport,
    prepareBackendReport,
    preparingReport,
    loadUpload,
    clearUpload,
    download,
    demo,
    startDemo,
    stopDemo,
  } = useReport();
  return (
    <div className="data-cluster" aria-label="Data and demo controls">
      <div className="data-cluster__row">
        {demo.active ? (
          <button type="button" className="btn btn--small" onClick={stopDemo}>
            Stop demo · {demo.step}/{demo.total}
          </button>
        ) : (
          <button type="button" className="btn btn--small" onClick={startDemo}>
            Demo replay ▸
          </button>
        )}
        <label className="btn btn--small data-cluster__upload">
          Upload JSON
          <input
            type="file"
            accept=".json,application/json"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) loadUpload(file);
              e.target.value = '';
            }}
          />
        </label>
        <button type="button" className="btn btn--small" onClick={download}>
          Download
        </button>
        {source === 'backend' && (
          <>
            <button type="button" className="btn btn--small" onClick={refreshBackendReport}>
              Refresh calls
            </button>
            <button
              type="button"
              className="btn btn--small"
              onClick={prepareBackendReport}
              disabled={preparingReport}
            >
              {preparingReport ? 'Researching…' : 'Prepare final report'}
            </button>
          </>
        )}
      </div>
      <p className="micro faint data-cluster__status">
        {source === 'demo' && 'Replaying a recorded negotiation'}
        {source === 'upload' && (
          <>
            Showing {uploadName}{' '}
            <button type="button" className="data-cluster__clear" onClick={clearUpload}>
              clear
            </button>
          </>
        )}
        {source === 'sample' && 'Showing the bundled sample run'}
        {source === 'backend' && `Live evidence · ${backendVersionId}`}
      </p>
      {uploadError && <p className="micro report-error">{uploadError}</p>}
      {backendError && <p className="micro report-error">{backendError}</p>}
    </div>
  );
}

function SpecStrip({ report }) {
  const spec = report.job_spec ?? {};
  const facts = spec.facts ?? {};
  const pairs = [
    ['From', firstClause(facts.origin) || '—'],
    ['To', firstClause(facts.destination) || '—'],
    ['When', facts.requested_date ?? '—'],
    ['Size', firstClause(facts.inventory, ':') || '—'],
  ];
  return (
    <section aria-labelledby="sec-move">
      <SectionHead no="01" title="Your move" headId="sec-move" />
      <div className="spec-strip">
        <p className="spec-strip__sentence">
          <MonoDigits text={moveSentence(report)} />
        </p>
        <dl className="spec-strip__grid">
          {pairs.map(([label, value]) => (
            <div key={label}>
              <dt className="micro muted">{label}</dt>
              <dd className="mono">
                <MonoDigits text={value} />
              </dd>
            </div>
          ))}
        </dl>
        <details className="fold">
          <summary>everything we confirmed</summary>
          <div className="fold-body">
            {Object.entries(facts).map(([key, value]) => (
              <p key={key} className="mono spec-strip__fact">
                <span className="micro muted">{key.replace(/_/g, ' ')} </span>
                {String(value)}
              </p>
            ))}
          </div>
        </details>
      </div>
      {humanDate(report.generated_at) && (
        <p className="micro faint report-stamp">
          Report generated {humanDate(report.generated_at)}
        </p>
      )}
    </section>
  );
}

function Decision({ report, ctx }) {
  if (!ctx) return null;
  const { rec, row, runnerUp, vsMedian, vsNextBinding } = ctx;
  const steps = report.next_steps ?? [];
  const firstPending = steps.findIndex((s) => (s.status ?? 'pending') === 'pending');
  const locked = String(row.binding).toLowerCase() === 'binding';

  return (
    <section aria-labelledby="sec-decision">
      <SectionHead no="02" title="The decision" headId="sec-decision" />
      <div className="decision">
        <div className="decision__main">
          {rec.provisional && (
            <p className="micro muted">Current leader — negotiation still in progress</p>
          )}
          <h3 className="decision__verb">
            {rec.provisional ? 'Leading: ' : 'Book '}
            <span className="decision__name">{rec.vendor_name ?? row.company}</span>
          </h3>
          {rec.headline && !rec.provisional && (
            <p className="decision__headline">
              <MonoDigits text={rec.headline} />
            </p>
          )}

          <dl className="decision__metrics">
            <div>
              <dt className="micro muted">Your price</dt>
              <dd className="mono">{money(row.final)}</dd>
            </div>
            <div>
              <dt className="micro muted">Locked in writing</dt>
              <dd className="mono">{locked ? 'YES' : 'NO — COULD CHANGE'}</dd>
            </div>
            <div>
              <dt className="micro muted">Saved on the call</dt>
              <dd>
                {row.won !== null && row.won > 0 ? (
                  <span className="delta delta--down">▾ {money(row.won)}</span>
                ) : (
                  <span className="delta delta--flat">▸ —</span>
                )}
              </dd>
            </div>
          </dl>

          <p className="decision__context mono muted">
            {row.won !== null && row.won > 0 && (
              <>negotiated down from their {money(row.initial)} opening</>
            )}
            {vsMedian !== null && vsMedian > 0 && <> · {money(vsMedian)} under the market median</>}
            {vsNextBinding !== null && vsNextBinding > 0 && (
              <>
                {' '}
                · {money(vsNextBinding)} under the next-best binding quote
              </>
            )}
          </p>

          {rec.rationale && (
            <details className="fold">
              <summary>why we're confident (and why the cheapest quote lost)</summary>
              <div className="fold-body">
                <p className="decision__rationale">
                  <MonoDigits text={rec.rationale} />
                </p>
              </div>
            </details>
          )}

          {steps.length > 0 && (
            <div className="decision__steps">
              <h4 className="micro muted">Do this now</h4>
              <ul className="checklist">
                {steps.map((step, i) => {
                  const status = step.status ?? 'pending';
                  const box = status === 'done' ? '[x]' : status === 'scheduled' ? '[~]' : '[ ]';
                  return (
                    <li
                      key={`${step.action}-${i}`}
                      className={status === 'done' ? 'done' : i === firstPending ? 'next' : ''}
                    >
                      <span className="box">{box}</span>
                      <span>
                        <MonoDigits text={step.action ?? ''} /> —{' '}
                        <span className="muted">due {step.due ?? '—'}</span>
                      </span>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          <div className="decision__actions">
            <Link to={`/call/${rec.call_id ?? row.callId}`} className="btn">
              Read this call →
            </Link>
            {runnerUp && (
              <span className="mono muted decision__runner">
                Runner-up: {runnerUp.company} {money(runnerUp.final)}
                {String(runnerUp.binding).toLowerCase() === 'binding' ? ' binding' : ''}
              </span>
            )}
          </div>
        </div>

        <div className="decision__price" aria-label={`Final price ${money(row.final)}`}>
          <span className="micro muted">Final price</span>
          <span className="decision__price-num">{money(row.final)}</span>
          <span className="micro muted">{locked ? 'Binding — locked in writing' : 'Could change'}</span>
        </div>
      </div>
    </section>
  );
}

function RedFlagStamp({ report, ranking }) {
  const flagged = (report.calls ?? []).filter((c) => (c.quote?.red_flags ?? []).length);
  if (!flagged.length) return null;
  const pct = report.benchmarks?.red_flag_below_market_pct ?? 30;
  return (
    <div className="stamp" role="note" aria-label="Red flags">
      <h3 className="stamp__title">Red flags</h3>
      <p className="micro muted stamp__rule">
        The brief's own rule: any quote {pct}%+ below market is a warning sign, not a win.
      </p>
      {flagged.map((call) => {
        const row = ranking.find((r) => r.callId === call.call_id);
        return (
          <div key={call.call_id} className="stamp__vendor">
            <p className="mono">
              <strong>{call.vendor?.name}</strong> — quoted{' '}
              {money(call.quote?.estimated_total)}, ranked #{row?.rank ?? '—'} despite the
              lowest sticker:
            </p>
            <ul className="stamp__list mono">
              {(call.quote?.red_flags ?? []).map((flag) => {
                const at = heardAt(call, flag.evidence_event_id);
                return (
                  <li key={flag.key}>
                    <span className="stamp__flag-key">{(flag.key ?? '').replace(/_/g, ' ')}</span>{' '}
                    — <MonoDigits text={flag.detail ?? ''} />
                    {at && <span className="faint"> — heard at {at} on the call</span>}
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

function ScoringMath({ ranking }) {
  return (
    <>
      <div className="skeptic__scroll">
      <table className="ledger">
        <thead>
          <tr>
            <th>Rank</th>
            <th>Company</th>
            <th>Outcome</th>
            <th className="num">Opener</th>
            <th className="num">Final</th>
            <th className="num">Won</th>
            <th>Binding</th>
            <th className="num">Flags</th>
            <th className="num">Score</th>
          </tr>
        </thead>
        <tbody>
          {ranking.map((r) => (
            <tr key={r.callId}>
              <td>{r.rank}</td>
              <td>{r.company}</td>
              <td>{r.outcomeLabel}</td>
              <td className="num">{money(r.initial)}</td>
              <td className="num">{money(r.final)}</td>
              <td className="num">{r.won !== null && r.won > 0 ? <span className="delta delta--down">▾ {money(r.won)}</span> : '—'}</td>
              <td>{String(r.binding)}</td>
              <td className="num">{r.redFlags || '—'}</td>
              <td className="num">{r.score !== null ? money(r.score, 2) : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      <p className="micro muted skeptic__footnote">
        A quote's score is its price plus risk penalties — for prices not locked in
        writing, warning signs, refusing to itemize, pricing far below market, and
        oversized deposits. Lowest score wins. That's why the cheapest sticker isn't
        first; the exact penalties are itemized below.
      </p>
      {ranking
        .filter((r) => r.breakdown.length)
        .map((r) => (
          <div key={r.callId} className="skeptic__breakdown">
            <p className="mono">
              <strong>{r.company}</strong> — score {money(r.score, 2)}
            </p>
            <table className="ledger ledger--narrow">
              <tbody>
                {r.breakdown.map(([label, value]) => (
                  <tr key={label}>
                    <td>
                      <MonoDigits text={label} />
                    </td>
                    <td className="num">{money(value, 2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
    </>
  );
}

function FeeMatrixTable({ report }) {
  const { vendors, categories, cells, totals } = feeMatrix(report);
  if (!vendors.length) return null;
  return (
    <>
      <p className="micro muted">
        The same fee categories, side by side. A struck-through fee was removed during
        the call.
      </p>
      <div className="skeptic__scroll">
        <table className="ledger">
          <thead>
            <tr>
              <th>Category</th>
              {vendors.map((v) => (
                <th key={v} className="num">
                  {v}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {categories.map((cat) => (
              <tr key={cat}>
                <td>{cat}</td>
                {vendors.map((v) => {
                  const cell = cells.get(`${cat}::${v}`);
                  return (
                    <td key={v} className="num">
                      {cell ? (
                        <span className={cell.struck ? 'strike' : ''}>
                          {cell.text}
                          {cell.struck ? ' waived' : ''}
                        </span>
                      ) : (
                        '—'
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
            <tr>
              <td>
                <strong>TOTAL</strong>
              </td>
              {totals.map((t, i) => (
                <td key={vendors[i]} className="num">
                  <strong>{t}</strong>
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
    </>
  );
}

function EvidenceFeed({ report }) {
  const index = transcriptIndex(report);
  const entries = [];
  for (const call of report.calls ?? []) {
    const vendor = call.vendor?.name ?? '';
    for (const move of call.quote?.negotiation_moves ?? []) {
      entries.push({ kind: 'move', call, vendor, item: move });
    }
    for (const fee of call.quote?.killed_fees ?? []) {
      entries.push({ kind: 'fee', call, vendor, item: fee });
    }
  }
  if (!entries.length) {
    return <p className="mono muted">No price movement yet — the calls are still in progress.</p>;
  }
  return (
    <div className="evidence">
      <p className="micro muted">
        Each time a price moved, here's the before and after — and the exact words that
        did it.
      </p>
      {entries.map(({ kind, call, vendor, item }, i) => {
        const hit = index.get(item.evidence_event_id);
        const event = hit?.event ?? {};
        const stamp = mmss(event.timestamp_seconds);
        return (
          <div key={i} className="evidence__pull">
            <p className="mono evidence__head">
              <strong>{vendor}</strong>{' '}
              {kind === 'move' ? (
                <>
                  — {money(item.before)} → <strong>{money(item.after)}</strong>{' '}
                  <span className="delta delta--down">
                    ▾ {money((item.before ?? 0) - (item.after ?? 0))} won
                  </span>
                </>
              ) : (
                <>
                  — <span className="strike">{money(item.original_amount)}</span>{' '}
                  <strong>{item.category ?? 'fee'} killed</strong>
                </>
              )}
            </p>
            <p className="micro muted">
              How: {kind === 'move' ? item.lever ?? '—' : item.description ?? '—'}
            </p>
            {event.text && (
              <blockquote className="evidence__quote">
                <span className="evidence__stamp">{stamp ?? '—'}</span>
                <span className="evidence__text">“{event.text}”</span>
              </blockquote>
            )}
            {stamp && (
              <p className="micro faint">
                {call.recording_url ? (
                  <a href={`${call.recording_url}#t=${event.timestamp_seconds.toFixed(1)}`}>
                    Listen at {stamp} on the call
                  </a>
                ) : (
                  <>Heard at {stamp} on the call</>
                )}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function Report() {
  const { report, ranking, lastEvent } = useReport();

  const ctx = report ? decisionContext(report, ranking) : null;

  if (!report) {
    return (
      <main className="report">
        <div className="report__masthead">
          <h1 className="report__title">Your moving report</h1>
          <DataDemoCluster />
        </div>
        <p className="mono muted">
          No report yet. It appears here after your intake call — or upload a report
          file above.
        </p>
      </main>
    );
  }

  // The pick follows the decision context, so mid-demo the current leader's
  // entry, chart dot, and tag stay in amber sync with the hero (reference
  // behavior: render_decision returns rec_call_id for the whole page).
  const recCallId = ctx?.rec?.call_id ?? null;
  const calls = report.calls ?? [];
  const countWord = COUNT_WORDS[calls.length] ?? String(calls.length);

  return (
    <main className="report">
      <div className="report__masthead">
        <h1 className="report__title">Your moving report</h1>
        <DataDemoCluster />
      </div>

      <SpecStrip report={report} />
      <Decision report={report} ctx={ctx} />

      <section aria-labelledby="sec-calls">
        <SectionHead no="03" title={`The ${countWord} calls`} headId="sec-calls" />
        <p className="micro muted report__counts">{outcomeCounts(report)}</p>
        <CallsBlotter
          report={report}
          ranking={ranking}
          recCallId={recCallId}
          lastEvent={lastEvent}
        />
        <RedFlagStamp report={report} ranking={ranking} />
      </section>

      <section aria-labelledby="sec-skeptic">
        <SectionHead no="04" title="For the skeptical" headId="sec-skeptic" />
        <p className="micro muted">
          Don't take our word for it — every number here comes from what the companies
          said on the calls.
        </p>
        <MarketStrip report={report} ranking={ranking} recCallId={recCallId} />
        <details className="fold">
          <summary>how we ranked them</summary>
          <div className="fold-body">
            <ScoringMath ranking={ranking} />
          </div>
        </details>
        <details className="fold">
          <summary>every fee, side by side</summary>
          <div className="fold-body">
            <FeeMatrixTable report={report} />
          </div>
        </details>
        <details className="fold">
          <summary>every dollar we saved</summary>
          <div className="fold-body">
            <EvidenceFeed report={report} />
          </div>
        </details>
      </section>
    </main>
  );
}

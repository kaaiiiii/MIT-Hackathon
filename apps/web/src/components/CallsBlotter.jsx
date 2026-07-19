// CallsBlotter — the signature element. A grid of uniform ledger entries, one
// per call. In demo mode: an affected entry's price counts to its new total in
// place, the vendor's verbatim line types on beneath it, and the grid re-sorts
// exactly once with a FLIP transition. All of it collapses to instant state
// changes under prefers-reduced-motion. Plain rAF + CSS only.
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { money, orderCalls, quoteTotal, OUTCOME_LABEL } from '../lib/report';
import { countUp, runFlip, typeOn } from '../lib/motion';

function PriceCounter({ value }) {
  const [shown, setShown] = useState(value);
  const shownRef = useRef(value);
  useEffect(() => {
    if (value === shownRef.current) return undefined;
    const from = shownRef.current ?? value;
    shownRef.current = value;
    return countUp(from, value, (v) => setShown(v));
  }, [value]);
  return <span className="entry__final-num">{money(shown)}</span>;
}

function TypedLine({ event }) {
  const [text, setText] = useState('');
  useEffect(() => {
    if (!event?.text) return undefined;
    setText('');
    return typeOn(event.text, setText);
  }, [event]);
  if (!event?.text) return null;
  const speaker = (event.speaker ?? 'vendor').toUpperCase();
  // Screen readers get the full line once; the character-by-character
  // animation is aria-hidden so it doesn't re-announce every frame.
  return (
    <p className="entry__typed">
      <span className="entry__typed-who">{speaker} </span>
      <span aria-hidden="true">“{text}”</span>
      <span className="visually-hidden" aria-live="polite">
        {event.text}
      </span>
    </p>
  );
}

function Entry({ call, row, isPick, typedEvent }) {
  const quote = call.quote ?? {};
  const outcome = call.outcome ?? {};
  const otype = outcome.outcome_type;
  const total = quoteTotal(call);
  const initial = typeof quote.initial_total === 'number' ? quote.initial_total : null;
  const won = initial !== null && total !== null ? initial - total : null;
  const flags = quote.red_flags ?? [];
  const binding = (quote.binding_status || '').toLowerCase() === 'binding';

  const classes = ['entry'];
  if (isPick) classes.push('entry--pick');
  if (flags.length) classes.push('entry--flagged');

  return (
    <article className={classes.join(' ')}>
      <header className="entry__head">
        <span className="entry__rank">{row?.rank === '—' ? '··' : String(row?.rank ?? '·').padStart(2, '0')}</span>
        <h3 className="entry__name">{call.vendor?.name ?? call.call_id}</h3>
        {typeof call.vendor?.rating === 'number' && (
          <span className="mono muted entry__rating">{call.vendor.rating.toFixed(1)}</span>
        )}
      </header>

      <div className="entry__tags">
        {isPick && <span className="tag tag--pick">The pick</span>}
        <span className={`tag ${otype === 'declined' ? 'tag--struck' : otype === 'complete_quote' ? '' : 'tag--quiet'}`}>
          {OUTCOME_LABEL[otype] ?? 'IN PROGRESS'}
        </span>
      </div>

      {total !== null ? (
        <dl className="entry__prices">
          {initial !== null && initial !== total && (
            <div className="entry__price-row">
              <dt className="micro muted">Opener</dt>
              <dd className="mono strike">{money(initial)}</dd>
            </div>
          )}
          <div className="entry__price-row">
            <dt className="micro muted">Final</dt>
            <dd className="entry__final">
              <PriceCounter value={total} />
            </dd>
          </div>
          <div className="entry__price-row">
            <dt className="micro muted">{binding ? 'Locked' : 'Could change'}</dt>
            <dd>
              {won !== null && won > 0 ? (
                <span className="delta delta--down">▾ {money(won)} saved</span>
              ) : (
                <span className="delta delta--flat">▸ no movement</span>
              )}
            </dd>
          </div>
        </dl>
      ) : (
        <p className="entry__reason mono muted">
          {otype === 'callback_required'
            ? `${outcome.reason ?? 'They promised to call back with a firm number'} — we'll hold them to it.`
            : outcome.reason ?? 'No price on the call.'}
        </p>
      )}

      {flags.length > 0 && (
        <p className="entry__flags mono">
          {flags.length} warning sign{flags.length !== 1 ? 's' : ''} — read before booking
        </p>
      )}
      {(outcome.validation_warnings ?? []).map((w) => (
        <p key={w} className="mono muted entry__warning">
          {w}
        </p>
      ))}

      <TypedLine event={typedEvent} />

      <Link to={`/call/${call.call_id}`} className="btn btn--small entry__open">
        Read the call →
      </Link>
    </article>
  );
}

export default function CallsBlotter({ report, ranking, recCallId, lastEvent }) {
  const ordered = orderCalls(report, ranking);
  const rowById = new Map(ranking.map((r) => [r.callId, r]));
  const orderKey = ordered.map((c) => c.call_id).join('|');

  const refs = useRef(new Map());
  const prevRects = useRef(new Map());
  const prevOrderKey = useRef(orderKey);

  useLayoutEffect(() => {
    const els = [...refs.current.entries()].filter(([, el]) => el);
    if (prevOrderKey.current !== orderKey) {
      runFlip(prevRects.current, els);
      prevOrderKey.current = orderKey;
    }
    prevRects.current = new Map(els.map(([k, el]) => [k, el.getBoundingClientRect()]));
  });

  // The verbatim line types on the card the live event touched.
  const typedByCall =
    lastEvent?.type === 'transcript' && lastEvent.event?.text
      ? { callId: lastEvent.call_id, event: { ...lastEvent.event, seq: lastEvent.seq } }
      : null;

  return (
    <div className="blotter">
      {ordered.map((call) => (
        <div
          key={call.call_id}
          className="blotter__cell"
          ref={(el) => {
            if (el) refs.current.set(call.call_id, el);
            else refs.current.delete(call.call_id);
          }}
        >
          <Entry
            call={call}
            row={rowById.get(call.call_id)}
            isPick={call.call_id === recCallId}
            typedEvent={typedByCall?.callId === call.call_id ? typedByCall.event : null}
          />
        </div>
      ))}
    </div>
  );
}

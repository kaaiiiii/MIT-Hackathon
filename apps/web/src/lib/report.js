// report.js — pure data logic, a faithful port of report_dashboard.py.
// The Streamlit file is the behavior reference; keep the numbers identical.

export const PENALTY_NON_BINDING_PCT = 20;
export const PENALTY_PER_RED_FLAG = 150;
export const PENALTY_NO_ITEMIZATION = 250;
export const PENALTY_BELOW_MARKET = 300;
export const PENALTY_DEPOSIT_PER_10PCT = 50;

export const OUTCOME_LABEL = {
  complete_quote: 'FIRM QUOTE',
  callback_required: 'CALLBACK',
  declined: 'DECLINED',
  incomplete_quote: 'INCOMPLETE',
};

export function money(value, decimals = 0) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return (
      '$' +
      value.toLocaleString('en-US', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      })
    );
  }
  return '—';
}

export function quoteTotal(call) {
  const total = call?.quote?.estimated_total;
  return typeof total === 'number' && Number.isFinite(total) ? total : null;
}

export function firstClause(text, sep = ' — ') {
  return String(text ?? '').split(sep)[0].trim();
}

export function bindingWords(quote) {
  const status = (quote?.binding_status || '').toLowerCase();
  if (status === 'binding') return 'YES — IN WRITING';
  if (status) return 'NO — COULD CHANGE';
  return '—';
}

export function moveSentence(report) {
  const facts = report?.job_spec?.facts ?? {};
  let sentence = String(facts.service ?? 'Your move');
  const origin = firstClause(facts.origin);
  const destination = firstClause(facts.destination);
  if (origin && destination) sentence += ` from ${origin} to ${destination}`;
  if (facts.distance) sentence += ` — ${facts.distance}`;
  if (facts.requested_date) sentence += `, ${facts.requested_date}`;
  return sentence;
}

// ---- live events (mirror of apply_events) ----------------------------------

const EXTEND_KEYS = new Set(['negotiation_moves', 'line_items', 'killed_fees', 'red_flags']);

export function applyEvent(report, event) {
  const next = structuredClone(report);
  const call = (next.calls ?? []).find((c) => c.call_id === event.call_id);
  const kind = event.type;
  if (kind === 'transcript' && call) {
    (call.transcript ??= []).push(event.event ?? {});
  } else if (kind === 'quote_patch' && call) {
    const quote = (call.quote ??= {});
    for (const [key, value] of Object.entries(event.patch ?? {})) {
      if (EXTEND_KEYS.has(key)) {
        (quote[key] ??= []).push(...value);
      } else {
        quote[key] = value;
      }
    }
  } else if (kind === 'outcome' && call) {
    call.outcome = event.outcome ?? {};
    call.status = event.status ?? call.status;
  } else if (kind === 'recommendation') {
    next.recommendation = event.recommendation ?? {};
  }
  return next;
}

export function applyEvents(report, events) {
  return events.reduce((acc, ev) => applyEvent(acc, ev), report);
}

export function parseJsonl(text) {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .flatMap((line) => {
      try {
        return [JSON.parse(line)];
      } catch {
        return [];
      }
    });
}

// ---- scoring (mirror of score_call / build_ranking) --------------------------

export function scoreCall(call, benchmarks) {
  const quote = call?.quote ?? {};
  const total = quoteTotal(call);
  if (total === null) return { score: null, breakdown: [] };

  const breakdown = [['Quoted total', total]];

  const binding = (quote.binding_status || '').toLowerCase();
  if (binding !== 'binding') {
    breakdown.push([
      `Non-binding risk (+${PENALTY_NON_BINDING_PCT}%)`,
      Math.round(total * PENALTY_NON_BINDING_PCT) / 100,
    ]);
  }

  const flags = quote.red_flags ?? [];
  if (flags.length) {
    breakdown.push([
      `${flags.length} red flag(s) × $${PENALTY_PER_RED_FLAG}`,
      PENALTY_PER_RED_FLAG * flags.length,
    ]);
  }

  if (!(quote.line_items ?? []).length) {
    breakdown.push([`No itemization (+$${PENALTY_NO_ITEMIZATION})`, PENALTY_NO_ITEMIZATION]);
  }

  const pct = benchmarks?.red_flag_below_market_pct ?? 30;
  const median = benchmarks?.market_median;
  if (typeof median === 'number' && total < median * (1 - pct / 100)) {
    breakdown.push([`>${pct}% below market (+$${PENALTY_BELOW_MARKET})`, PENALTY_BELOW_MARKET]);
  }

  const depositPct = quote.deposit_pct;
  if (typeof depositPct === 'number' && depositPct > 20) {
    const penalty = Math.round(((depositPct - 20) / 10) * PENALTY_DEPOSIT_PER_10PCT * 100) / 100;
    breakdown.push([`Deposit ${Math.round(depositPct)}% (> 20%)`, penalty]);
  }

  const score = Math.round(breakdown.reduce((s, [, v]) => s + v, 0) * 100) / 100;
  return { score, breakdown };
}

export function buildRanking(report) {
  const benchmarks = report?.benchmarks ?? {};
  const rows = (report?.calls ?? []).map((call) => {
    const quote = call.quote ?? {};
    const { score, breakdown } = scoreCall(call, benchmarks);
    const outcomeType = call.outcome?.outcome_type ?? 'incomplete_quote';
    const initial = typeof quote.initial_total === 'number' ? quote.initial_total : null;
    const total = quoteTotal(call);
    return {
      callId: call.call_id,
      company: call.vendor?.name ?? 'Unknown vendor',
      outcomeType,
      outcomeLabel: OUTCOME_LABEL[outcomeType] ?? outcomeType.toUpperCase(),
      initial,
      final: total,
      won: initial !== null && total !== null ? initial - total : null,
      binding: quote.binding_status ?? '—',
      deposit: quote.deposit ?? '—',
      redFlags: (quote.red_flags ?? []).length,
      score,
      breakdown,
    };
  });

  const priced = rows.filter((r) => r.score !== null);
  priced.sort((a, b) => a.score - b.score); // Array.sort is stable
  const unpriced = rows.filter((r) => r.score === null);
  const ordered = [...priced, ...unpriced];
  let n = 0;
  for (const row of ordered) {
    row.rank = row.score !== null ? String(++n) : '—';
  }
  return ordered;
}

// Best deal first, then promised callbacks, then declines (mirror of grid order).
export function orderCalls(report, ranking) {
  const calls = report?.calls ?? [];
  const byId = new Map(calls.map((c) => [c.call_id, c]));
  const pricedIds = ranking.filter((r) => r.score !== null).map((r) => r.callId);
  const ordered = pricedIds.map((id) => byId.get(id)).filter(Boolean);
  const rest = calls.filter((c) => !pricedIds.includes(c.call_id));
  rest.sort((a, b) => {
    const w = (c) => (c.outcome?.outcome_type === 'callback_required' ? 0 : 1);
    return w(a) - w(b);
  });
  return [...ordered, ...rest];
}

export function outcomeCounts(report) {
  const counts = {};
  for (const call of report?.calls ?? []) {
    const t = call.outcome?.outcome_type ?? 'in_progress';
    counts[t] = (counts[t] ?? 0) + 1;
  }
  const parts = [];
  if (counts.complete_quote) {
    parts.push(`${counts.complete_quote} FIRM QUOTE${counts.complete_quote !== 1 ? 'S' : ''}`);
  }
  if (counts.callback_required) {
    parts.push(`${counts.callback_required} CALLBACK${counts.callback_required !== 1 ? 'S' : ''}`);
  }
  if (counts.declined) parts.push(`${counts.declined} DECLINED`);
  if (counts.incomplete_quote) parts.push(`${counts.incomplete_quote} INCOMPLETE`);
  return parts.join(' · ');
}

// ---- evidence & transcripts ----------------------------------------------------

export function evidenceEventIds(call) {
  const quote = call?.quote ?? {};
  const ids = new Set();
  for (const group of ['line_items', 'red_flags', 'negotiation_moves', 'killed_fees']) {
    for (const item of quote[group] ?? []) {
      if (item.evidence_event_id) ids.add(item.evidence_event_id);
    }
  }
  for (const v of Object.values(quote.evidence ?? {})) {
    if (v) ids.add(v);
  }
  return ids;
}

// event_id -> plain-language caption when money moved on that line.
export function momentNotes(call) {
  const quote = call?.quote ?? {};
  const notes = new Map();
  for (const move of quote.negotiation_moves ?? []) {
    const ev = move.evidence_event_id;
    const won = (move.before ?? 0) - (move.after ?? 0);
    if (ev && won > 0) notes.set(ev, `▾ ${money(won)} — THIS LINE MOVED THE PRICE`);
  }
  for (const fee of quote.killed_fees ?? []) {
    const ev = fee.evidence_event_id;
    if (ev) {
      notes.set(
        ev,
        `▾ ${money(fee.original_amount)} ${(fee.category ?? 'fee').toUpperCase()} KILLED ON THIS LINE`
      );
    }
  }
  return notes;
}

// Seconds -> "1:34" — the human way to point at a moment in a call.
export function mmss(seconds) {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return null;
  const total = Math.round(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

// Human-readable pointer for an evidence event: where to hear it on the call.
export function heardAt(call, eventId) {
  if (!eventId) return null;
  const event = (call?.transcript ?? []).find((e) => e.event_id === eventId);
  return mmss(event?.timestamp_seconds);
}

export function humanDate(iso) {
  const d = new Date(iso ?? '');
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' });
}

export function transcriptIndex(report) {
  const index = new Map();
  for (const call of report?.calls ?? []) {
    for (const event of call.transcript ?? []) {
      if (event.event_id) index.set(event.event_id, { call, event });
    }
  }
  return index;
}

// ---- decision context (honest savings framing) -----------------------------------

export function decisionContext(report, ranking) {
  const rec = report?.recommendation ?? null;
  let row = null;
  let effective = rec;
  if (rec?.call_id) row = ranking.find((r) => r.callId === rec.call_id) ?? null;
  if (!row) {
    const priced = ranking.filter((r) => r.score !== null);
    if (priced.length) {
      row = priced[0];
      effective = {
        call_id: row.callId,
        vendor_name: row.company,
        headline: 'Current leader (negotiation still in progress)',
        rationale: 'Live run — the recommendation locks when the final call closes.',
        provisional: true,
      };
    }
  }
  if (!row) return null;

  const priced = ranking.filter((r) => r.score !== null);
  const runnerUp = priced.find((r) => r.callId !== row.callId) ?? null;
  const median = report?.benchmarks?.market_median ?? null;
  const vsMedian =
    typeof median === 'number' && row.final !== null ? median - row.final : null;
  const nextBinding = priced.find(
    (r) =>
      r.callId !== row.callId &&
      (r.binding || '').toLowerCase() === 'binding' &&
      r.final !== null
  );
  const vsNextBinding =
    nextBinding && row.final !== null ? nextBinding.final - row.final : null;

  return { rec: effective, row, runnerUp, vsMedian, vsNextBinding, nextBinding };
}

// ---- fee matrix -------------------------------------------------------------------

export function feeMatrix(report) {
  const vendors = [];
  const cells = new Map(); // `${cat}::${vendor}` -> {text, struck}
  const categories = [];
  for (const call of report?.calls ?? []) {
    const quote = call.quote ?? {};
    const items = quote.line_items ?? [];
    const killed = quote.killed_fees ?? [];
    if (!items.length && !killed.length && quoteTotal(call) === null) continue;
    const vendor = call.vendor?.name ?? call.call_id;
    vendors.push(vendor);
    if (!items.length) {
      if (!categories.includes('(refused to itemize)')) categories.push('(refused to itemize)');
      cells.set(`(refused to itemize)::${vendor}`, { text: 'REFUSED', struck: false });
    }
    for (const item of items) {
      const cat = item.category ?? 'other';
      if (!categories.includes(cat)) categories.push(cat);
      cells.set(`${cat}::${vendor}`, { text: money(item.amount), struck: false });
    }
    for (const fee of killed) {
      const cat = fee.category ?? 'other';
      if (!categories.includes(cat)) categories.push(cat);
      cells.set(`${cat}::${vendor}`, {
        text: money(fee.original_amount),
        struck: true,
      });
    }
  }
  const totals = vendors.map((v) => {
    const call = (report?.calls ?? []).find((c) => (c.vendor?.name ?? c.call_id) === v);
    return money(quoteTotal(call));
  });
  return { vendors, categories, cells, totals };
}

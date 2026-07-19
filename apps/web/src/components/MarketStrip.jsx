// MarketStrip — 1-D strip plot, hand-rolled SVG. Furniture (band, median and
// red-flag rules, ticks, labels) is neutral ink; the dots are the only data
// color: paper for quotes, amber for the pick, red for the flagged lowball.
// Identity is never color-alone: every dot carries a direct mono label.
//
// Quotes cluster at the cheap end of the market scale, so dot labels stack in
// rows below the axis, ordered by descending x with each label extending
// rightward from its dot — leader lines can never cross another dot's text.
import { money } from '../lib/report';

const W = 860;
const PAD = 56;
const BASE = 116;
const ROW0 = BASE + 58;
const ROW_GAP = 22;

export default function MarketStrip({ report, ranking, recCallId }) {
  const b = report?.benchmarks ?? {};
  const priced = ranking.filter((r) => r.final !== null);
  if (!priced.length) return null;

  const H = ROW0 + ROW_GAP * (priced.length - 1) + 16;
  const values = [b.market_low, b.market_high, ...priced.map((r) => r.final)].filter(
    (v) => typeof v === 'number'
  );
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;
  const x = (v) => PAD + ((v - lo) / span) * (W - 2 * PAD);

  const median = b.market_median;
  const flagPct = b.red_flag_below_market_pct ?? 30;
  const flagLine = typeof median === 'number' ? median * (1 - flagPct / 100) : null;

  const dotFill = (r) => {
    if (r.callId === recCallId) return 'var(--amber)';
    if (r.redFlags > 0) return 'var(--red)';
    return 'var(--paper-0)';
  };

  // Row k holds the k-th most expensive quote; labels extend right of the dot.
  const byXDesc = [...priced].sort((a, c) => c.final - a.final);

  return (
    <figure className="chart" role="img" aria-label="Where the quotes sit against the market">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: 'block' }}>
        {/* fair range band */}
        {typeof b.fair_range_low === 'number' && (
          <g>
            <rect
              x={x(b.fair_range_low)}
              y={BASE - 36}
              width={x(b.fair_range_high) - x(b.fair_range_low)}
              height={72}
              fill="var(--ink-2)"
              stroke="var(--line-0)"
            />
            <text
              x={x(b.fair_range_high) + 8}
              y={BASE - 44}
              className="chart-micro"
              fill="var(--paper-2)"
            >
              FAIR RANGE {money(b.fair_range_low)}–{money(b.fair_range_high)}
            </text>
          </g>
        )}

        {/* median + red-flag reference rules — furniture, muted */}
        {flagLine !== null && (
          <g>
            <line
              x1={x(flagLine)}
              y1={BASE - 56}
              x2={x(flagLine)}
              y2={BASE + 44}
              stroke="var(--line-1)"
              strokeDasharray="2 4"
            />
            <text x={x(flagLine) + 5} y={20} className="chart-micro" fill="var(--paper-2)">
              RED-FLAG LINE −{flagPct}%
            </text>
          </g>
        )}
        {typeof median === 'number' && (
          <g>
            <line
              x1={x(median)}
              y1={BASE - 56}
              x2={x(median)}
              y2={BASE + 44}
              stroke="var(--line-1)"
              strokeDasharray="5 4"
            />
            <text x={x(median) + 5} y={38} className="chart-micro" fill="var(--paper-2)">
              MEDIAN {money(median)}
            </text>
          </g>
        )}

        {/* baseline: collected market spread, end ticks */}
        <line x1={x(lo)} y1={BASE} x2={x(hi)} y2={BASE} stroke="var(--line-1)" />
        <line x1={x(lo)} y1={BASE - 7} x2={x(lo)} y2={BASE + 7} stroke="var(--paper-2)" />
        <line x1={x(hi)} y1={BASE - 7} x2={x(hi)} y2={BASE + 7} stroke="var(--paper-2)" />

        {/* quote dots with stacked direct labels */}
        {byXDesc.map((r, k) => {
          const rowY = ROW0 + ROW_GAP * k;
          const nearRightEdge = x(r.final) > W - 180;
          const short = r.company.split(' ')[0].toUpperCase();
          const suffix =
            r.callId === recCallId ? ' — THE PICK' : r.redFlags > 0 ? ' — RED-FLAGGED' : '';
          return (
            <g key={r.callId}>
              <line
                x1={x(r.final)}
                y1={BASE + 8}
                x2={x(r.final)}
                y2={rowY - 11}
                stroke="var(--line-1)"
              />
              <circle
                cx={x(r.final)}
                cy={BASE}
                r={6}
                fill={dotFill(r)}
                stroke="var(--ink-0)"
                strokeWidth="2"
              >
                <title>{`${r.company}: ${money(r.final)}${suffix.toLowerCase()}`}</title>
              </circle>
              <text
                x={nearRightEdge ? x(r.final) - 8 : x(r.final) + 8}
                y={rowY}
                className="chart-micro"
                fill="var(--paper-1)"
                textAnchor={nearRightEdge ? 'end' : 'start'}
              >
                {short} {money(r.final)}
                {suffix}
              </text>
            </g>
          );
        })}
      </svg>
      <figcaption className="micro muted">
        Real quotes for this exact move ran {money(b.market_low)}–{money(b.market_high)}.
        The shaded band is a fair price; anything left of the dotted line is suspiciously
        cheap. Source: {String(b.source ?? 'not provided').split(' — ')[0]}.
      </figcaption>
    </figure>
  );
}

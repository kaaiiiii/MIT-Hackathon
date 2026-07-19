// PriceChart — per-seller price movement, single-series hand-rolled SVG line.
// The line is neutral paper; the drops between steps are annotated in green
// (money moving down is the only thing green is allowed to mean).
import { money } from '../lib/report';

const W = 860;
const H = 250;
const PAD_X = 56;
const PAD_TOP = 40;
const PAD_BOT = 56;

export default function PriceChart({ call, isPick }) {
  const moves = call?.quote?.negotiation_moves ?? [];
  if (!moves.length) return null;

  const steps = [
    { label: 'OPENING', value: moves[0].before },
    ...moves.map((m) => ({ label: m.lever ?? 'MOVE', value: m.after })),
  ].filter((s) => typeof s.value === 'number');
  if (steps.length < 2) return null;

  const values = steps.map((s) => s.value);
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;
  const x = (i) => PAD_X + (i / (steps.length - 1)) * (W - 2 * PAD_X);
  const y = (v) => PAD_TOP + (1 - (v - lo) / span) * (H - PAD_TOP - PAD_BOT);

  const path = steps.map((s, i) => `${i ? 'L' : 'M'}${x(i)},${y(s.value)}`).join(' ');
  const truncate = (t, n = 30) => (t.length > n ? `${t.slice(0, n - 1)}…` : t);

  return (
    <figure className="chart" role="img" aria-label={`How ${call.vendor?.name ?? 'the'} price moved`}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: 'block' }}>
        <line x1={PAD_X} y1={H - PAD_BOT} x2={W - PAD_X} y2={H - PAD_BOT} stroke="var(--line-1)" />
        <path d={path} fill="none" stroke="var(--paper-0)" strokeWidth="2" />
        {steps.map((s, i) => {
          const drop = i > 0 ? steps[i - 1].value - s.value : 0;
          const last = i === steps.length - 1;
          return (
            <g key={i}>
              <circle
                cx={x(i)}
                cy={y(s.value)}
                r={5.5}
                fill={last && isPick ? 'var(--amber)' : 'var(--paper-0)'}
                stroke="var(--ink-0)"
                strokeWidth="2"
              >
                <title>{`${s.label}: ${money(s.value)}`}</title>
              </circle>
              <text
                x={x(i)}
                y={y(s.value) - 14}
                className="chart-micro"
                fill="var(--paper-0)"
                textAnchor="middle"
              >
                {money(s.value)}
              </text>
              {drop > 0 && (
                <text
                  x={(x(i) + x(i - 1)) / 2}
                  y={(y(s.value) + y(steps[i - 1].value)) / 2 - 8}
                  className="chart-micro"
                  fill="var(--green)"
                  textAnchor="middle"
                >
                  ▾ {money(drop)}
                </text>
              )}
              <text
                x={x(i)}
                y={H - PAD_BOT + 20}
                className="chart-micro"
                fill="var(--paper-3)"
                textAnchor={i === 0 ? 'start' : last ? 'end' : 'middle'}
              >
                {truncate(s.label.toUpperCase())}
              </text>
            </g>
          );
        })}
      </svg>
    </figure>
  );
}

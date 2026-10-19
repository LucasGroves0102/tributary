import { money, quarter } from "../../format";
import type { FinancialRow } from "../../types";

const W = 640;
const H = 220;
const PAD = { top: 16, right: 12, bottom: 28, left: 88 };

/** Quarterly bars for one metric, oldest first; each bar links to the filing it came from. */
export function Chart({ rows, label }: { rows: FinancialRow[]; label: string }) {
  if (!rows.length) return <p className="muted">No data for {label.toLowerCase()}.</p>;
  const points = [...rows].sort((a, b) => a.period_end.localeCompare(b.period_end));
  const values = points.map((r) => Number(r.value));
  const max = Math.max(0, ...values);
  const min = Math.min(0, ...values);
  const span = max - min || 1;
  const plotH = H - PAD.top - PAD.bottom;
  const y = (v: number) => PAD.top + ((max - v) / span) * plotH;
  const slot = (W - PAD.left - PAD.right) / points.length;
  const barW = Math.min(36, slot * 0.6);
  const unit = points[0]!.unit;

  return (
    <svg
      className="chart"
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-label={`${label} by quarter`}
    >
      {[max, min !== 0 ? min : null, 0]
        .filter((v): v is number => v !== null)
        .map((v) => (
          <g key={v} className="gridline">
            <line x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} />
            <text x={PAD.left - 8} y={y(v)} dy="0.35em" textAnchor="end">
              {money(v, unit)}
            </text>
          </g>
        ))}
      {points.map((r, i) => {
        const v = Number(r.value);
        const x = PAD.left + slot * i + (slot - barW) / 2;
        const top = y(Math.max(v, 0));
        const bar = (
          <rect
            className={v < 0 ? "bar negative" : r.value_source === "derived" ? "bar derived" : "bar"}
            x={x}
            y={top}
            width={barW}
            height={Math.max(1, Math.abs(y(v) - y(0)))}
          >
            <title>
              {`${quarter(r.period_end)}: ${money(v, r.unit)} (${r.value_source}), ` +
                `${r.citation.form} filed ${r.citation.filed}`}
            </title>
          </rect>
        );
        return (
          <g key={r.period_end}>
            {r.citation.filing_url ? (
              <a href={r.citation.filing_url} target="_blank" rel="noopener noreferrer">
                {bar}
              </a>
            ) : (
              bar
            )}
            <text
              className={(points.length - 1 - i) % 2 ? "axis alternate" : "axis"}
              x={x + barW / 2}
              y={H - 8}
              textAnchor="middle"
            >
              {quarter(r.period_end)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

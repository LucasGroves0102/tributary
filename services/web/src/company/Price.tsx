/** The company page's Price tab ("Connect your data"): the user's own end-of-day closes and a
 * Valuation panel, both from `/v1/companies/{id}/prices` and `/valuation`, fetched with the
 * user's own market-data key. Without a connection it shows only a prompt to connect, never a
 * price. Formulas (Query API query_api/valuation.py, README "Connect your data"):
 * market cap = close × shares outstanding; P/E = market cap / TTM net income; P/S = market
 * cap / TTM revenue; EV/EBIT = (market cap + net debt) / TTM operating income. Every figure
 * shows its own date: the close's day, the shares' and the TTM figures' period ends. */

import { useMemo, useState, type MouseEvent } from "react";

import { pricesOf, valuationOf } from "../api";
import { Empty, ErrorPanel, Skeleton } from "../components/States";
import { day, money } from "../format";
import { href } from "../route";
import type { PriceRow, Valuation, ValuationMetric } from "../types";
import { isConnected, useMarketData } from "../market/useMarketData";
import { useLoad } from "./useLoad";

export const PRICE_RANGES: [string, number | null][] = [["1y", 1], ["5y", 5], ["Max", null]];

/** The rows from `years` before the newest one (null: all). */
export function inRange(rows: PriceRow[], years: number | null): PriceRow[] {
  if (years === null || !rows.length) return rows;
  const last = rows.at(-1)!.day;
  const from = `${Number(last.slice(0, 4)) - years}${last.slice(4)}`;
  return rows.filter((r) => r.day >= from);
}

/** "$4.93T"; ratios as "49.7×"; null as an em dash with its reason in the title. */
const multiple = (v: string | null) => (v === null ? "—" : `${Number(v).toFixed(1)}×`);

export function ConnectPrompt({ compact = false }: { compact?: boolean }) {
  return (
    <Empty action={<a className="btn sm" href={href({ view: "settings" })}>Connect your market data</a>}>
      {compact
        ? "Share prices come from your own market-data account."
        : "Prices and valuation multiples need your own market-data API key: Tributary fetches prices with it, keeps them for you only, and never shows them to anyone else."}
    </Empty>
  );
}

export function Price({ token, companyId, name }: { token: string; companyId: string; name: string }) {
  const market = useMarketData(token);
  const connected = isConnected(market);
  if (market === null) return <Skeleton chart label="Loading" />;
  if (!market.enabled) return null; // the tab isn't offered (Company.tsx); reached by URL
  if (!connected) {
    return (
      <section className="card" aria-label="Price">
        <div className="card-h"><h2>Price</h2></div>
        <ConnectPrompt />
      </section>
    );
  }
  return <Connected token={token} companyId={companyId} name={name} />;
}

function Connected({ token, companyId, name }: { token: string; companyId: string; name: string }) {
  const prices = useLoad(`prices:${companyId}`, () => pricesOf(token, companyId));
  const valuation = useLoad(`valuation:${companyId}`, () => valuationOf(token, companyId));
  const [range, setRange] = useState<number | null>(5);

  return (
    <div className="grid2 price-tab">
      <section className="card" aria-label="Price chart">
        <div className="card-h">
          <h2 className="grow">{name}: daily close</h2>
          <div className="row" role="group" aria-label="Range">
            {PRICE_RANGES.map(([label, years]) => (
              <button key={label} className="btn sm" aria-pressed={range === years} onClick={() => setRange(years)}>
                {label}
              </button>
            ))}
          </div>
        </div>
        {prices === null || prices.state === "loading" ? (
          <Skeleton chart label="Loading prices" />
        ) : prices.state === "denied" ? (
          <p className="empty">Your plan doesn't cover this company.</p>
        ) : prices.state === "error" ? (
          <ErrorPanel what="prices" detail={prices.message} onRetry={prices.retry} />
        ) : prices.data.status === "no_ticker" ? (
          <p className="empty">This company has no current ticker, so there are no prices to fetch.</p>
        ) : prices.data.status === "not_found" || !prices.data.rows.length ? (
          <p className="empty">Your provider has no prices for {prices.data.ticker ?? "this ticker"}.</p>
        ) : (
          <>
            <PriceChart rows={inRange(prices.data.rows, range)} label={`${name} (${prices.data.ticker}) daily close`} />
            <p className="foot">
              {prices.data.attribution} {prices.data.ticker}, close as traded (not adjusted).
              {prices.data.fetched_at && <> Fetched {day(prices.data.fetched_at.slice(0, 10), true)}.</>}
              {prices.data.warning && <> {prices.data.warning}.</>}
            </p>
          </>
        )}
      </section>
      <section className="card" aria-label="Valuation">
        <div className="card-h"><h2>Valuation</h2></div>
        {valuation === null || valuation.state === "loading" ? (
          <Skeleton lines={5} label="Loading valuation" />
        ) : valuation.state === "denied" ? (
          <p className="empty">Your plan doesn't cover this company's financials.</p>
        ) : valuation.state === "error" ? (
          <ErrorPanel what="the valuation" detail={valuation.message} onRetry={valuation.retry} />
        ) : (
          <ValuationPanel v={valuation.data} />
        )}
      </section>
    </div>
  );
}

const INPUT_LABELS: Record<ValuationMetric, string> = {
  shares_outstanding: "Shares outstanding",
  net_income: "Net income (TTM)",
  revenue: "Revenue (TTM)",
  operating_income: "Operating income (TTM)",
  net_debt: "Net debt",
};

export function ValuationPanel({ v }: { v: Valuation }) {
  if (!v.market_cap || !v.price) {
    return <p className="empty">{v.note ?? "No valuation yet."}</p>;
  }
  const m = v.multiples;
  const tiles: [string, string, string | null, string][] = [
    ["Market cap", money(Number(v.market_cap.value), "USD"), null, v.formulas.market_cap ?? ""],
    ["P/E", multiple(m.pe?.value ?? null), m.pe?.note ?? null, v.formulas.pe ?? ""],
    ["P/S", multiple(m.ps?.value ?? null), m.ps?.note ?? null, v.formulas.ps ?? ""],
    ["EV/EBIT", multiple(m.ev_ebit?.value ?? null), m.ev_ebit?.note ?? null, v.formulas.ev_ebit ?? ""],
  ];
  return (
    <>
      <div className="kv">
        {tiles.map(([k, value, note, formula]) => (
          <div key={k} title={formula}>
            <div className="k">{k}</div>
            <div className="v">{value}</div>
            <div className="p">{note ?? formula}</div>
          </div>
        ))}
      </div>
      <table className="t valuation-inputs">
        <caption className="visually-hidden">Inputs and their dates</caption>
        <tbody>
          <tr>
            <th scope="row">Close ({v.price.ticker})</th>
            <td className="n">${Number(v.price.close).toFixed(2)}</td>
            <td className="faint">{day(v.price.day, true)}</td>
          </tr>
          {(Object.keys(INPUT_LABELS) as ValuationMetric[]).map((k) => {
            const f = v.inputs[k];
            return (
              <tr key={k}>
                <th scope="row">{INPUT_LABELS[k]}</th>
                <td className="n">
                  {f ? (k === "shares_outstanding" ? `${(Number(f.value) / 1e9).toFixed(2)}B` : money(Number(f.value), "USD")) : "—"}
                </td>
                <td className="faint">
                  {f ? (
                    <>
                      {f.citation.filing_url ? (
                        <a href={f.citation.filing_url} target="_blank" rel="noreferrer">{f.citation.form}</a>
                      ) : f.citation.form}{" "}
                      period ending {day(f.period_end, true)}
                    </>
                  ) : "not in the filings"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="foot">
        Latest close × latest reported shares; TTM figures from the latest 10-Q/10-K. P/E uses TTM net
        income (no TTM diluted EPS in the filings data); EBIT is operating income.
        {v.share_classes && <> {`Several share classes (${v.share_classes.join(", ")}): ${v.price.ticker}'s price × total shares, approximate.`}</>}
        {" "}{v.attribution}
      </p>
    </>
  );
}

const W = 640;
const H = 220;
const PAD = { l: 8, r: 64, t: 10, b: 22 };

/** Daily closes as one line: min/max on the right, first/last day below, a readout on hover. */
export function PriceChart({ rows, label }: { rows: PriceRow[]; label: string }) {
  const pts = useMemo(() => rows.map((r) => ({ day: r.day, y: Number(r.close) })).filter((p) => Number.isFinite(p.y)), [rows]);
  const [hover, setHover] = useState<number | null>(null);
  if (pts.length < 2) return <p className="empty">Not enough prices to draw yet.</p>;
  const lo = Math.min(...pts.map((p) => p.y));
  const hi = Math.max(...pts.map((p) => p.y));
  const span = hi - lo || 1;
  const x = (i: number) => PAD.l + (i / (pts.length - 1)) * (W - PAD.l - PAD.r);
  const y = (v: number) => PAD.t + (1 - (v - lo) / span) * (H - PAD.t - PAD.b);
  const line = pts.map((p, i) => `${x(i).toFixed(1)},${y(p.y).toFixed(1)}`).join(" ");
  const last = pts.at(-1)!;
  const onMove = (e: MouseEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    if (!box.width) return;
    const vx = ((e.clientX - box.left) * W) / box.width;
    const i = Math.round(((vx - PAD.l) / (W - PAD.l - PAD.r)) * (pts.length - 1));
    setHover(Math.max(0, Math.min(pts.length - 1, i)));
  };
  const h = hover === null ? null : pts[hover]!;
  return (
    <figure className="price-chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${label}: ${pts[0]!.day} to ${last.day}, low $${lo.toFixed(2)}, high $${hi.toFixed(2)}, last $${last.y.toFixed(2)}`}
        onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        <polyline className="price-line" points={line} fill="none" />
        <text className="price-axis" x={W - PAD.r + 6} y={y(hi) + 4}>${hi.toFixed(2)}</text>
        <text className="price-axis" x={W - PAD.r + 6} y={y(lo) + 4}>${lo.toFixed(2)}</text>
        <text className="price-axis" x={PAD.l} y={H - 6}>{day(pts[0]!.day, true)}</text>
        <text className="price-axis" x={W - PAD.r} y={H - 6} textAnchor="end">{day(last.day, true)}</text>
        {h && hover !== null && (
          <>
            <line className="price-cursor" x1={x(hover)} x2={x(hover)} y1={PAD.t} y2={H - PAD.b} />
            <circle className="price-dot" cx={x(hover)} cy={y(h.y)} r={3} />
          </>
        )}
      </svg>
      <figcaption className="small" aria-live="polite">
        {h ? `${day(h.day, true)}: $${h.y.toFixed(2)}` : `Last close ${day(last.day, true)}: $${last.y.toFixed(2)}`}
      </figcaption>
    </figure>
  );
}

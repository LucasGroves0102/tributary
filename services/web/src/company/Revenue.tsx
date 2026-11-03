import { useMemo, useState } from "react";

import { financialsView, segmentsOf } from "../api";
import type { ChartState } from "../chart/model";
import type { SegmentAxisKind } from "../chart/segments";
import { change, money, pct } from "../format";
import { ChartView } from "../reports/Embeds";
import type { FinancialRow, SegmentRow } from "../types";
import { SaveChart } from "../workspaces/SaveChart";
import type { Saved } from "../workspaces/useSaved";
import {
  AXIS_TITLES,
  axesOf,
  axisChart,
  axisTable,
  COVERAGE_TOLERANCE,
  decompose,
  decompositionChart,
  PERIODS,
  QUALITY_FLAG_POINTS,
  qualityChart,
  revenueQuality,
  SHOWN,
  totalsByKey,
  type AxisTable,
  type View,
} from "./revenue";
import { Gate } from "./Tables";
import { useLoad } from "./useLoad";

interface Props {
  token: string;
  companyId: string;
  name: string;
  ticker: string;
  saved: Saved | null;
  onOpenChart?: (s: ChartState) => void;
}

/** quarters asked for: the table's PERIODS plus a year for the YoY comparisons */
const QUARTERS = PERIODS + 4;
const everyMetric = () => true;
const points = (v: number) => `${v >= -0.05 ? "+" : "−"}${Math.abs(v).toFixed(1)} pts`; // −0.0 reads as +0.0

/** The Revenue tab: revenue by segment and geography over time, growth decomposition and
 * revenue quality (company/revenue.ts has the rules). Every chart is a chart state: it can be
 * saved to a workspace (and embedded in a thesis) or opened in the chart builder. Values need
 * financials access, like Statements. */
export function Revenue({ token, companyId, name, ticker, saved, onOpenChart }: Props) {
  const [view, setView] = useState<View>("ttm");
  const ttm = useLoad(`${companyId}|ttm`, () =>
    Promise.all([
      segmentsOf(token, companyId, { metric: "revenue", quarters: QUARTERS, view: "ttm", basis: "fiscal" }),
      financialsView(token, companyId, QUARTERS, ["revenue", "receivables"], "ttm"),
    ]),
  );
  const quarter = useLoad(view === "quarter" ? `${companyId}|quarter` : null, () =>
    Promise.all([
      segmentsOf(token, companyId, { metric: "revenue", quarters: PERIODS, view: "quarter", basis: "fiscal" }),
      financialsView(token, companyId, PERIODS, ["revenue"], "quarter"),
    ]),
  );
  const label = (id: string) => (id === companyId ? ticker : id);
  const shown = view === "quarter" ? quarter : ttm;

  return (
    <>
      <section className="card">
        <div className="card-h wrap">
          <h2>Revenue by segment and geography</h2>
          <span className="seg" role="group" aria-label="Period">
            {(["ttm", "quarter"] as View[]).map((v) => (
              <button key={v} className={v === view ? "on" : undefined} aria-pressed={v === view} onClick={() => setView(v)}>
                {v === "ttm" ? "TTM" : "Quarterly"}
              </button>
            ))}
          </span>
          <span className="sub">fiscal periods · USD · as reported in the filings' XBRL; hover a value for its filing</span>
        </div>
        <Gate load={shown} name={name} what="Financials">
          {() => {
            const [seg, fin] = (shown as { state: "ok"; data: [{ rows: SegmentRow[] }, { rows: FinancialRow[] }] }).data;
            return <Axes rows={seg.rows} totals={totalsByKey(fin.rows, "revenue")} view={view} {...{ companyId, ticker, name, saved, onOpenChart, token, label }} />;
          }}
        </Gate>
      </section>
      <Gate load={ttm} name={name} what="Financials">
        {() => {
          const [seg, fin] = (ttm as { state: "ok"; data: [{ rows: SegmentRow[] }, { rows: FinancialRow[] }] }).data;
          return (
            <>
              <Decomposition rows={seg.rows} totals={totalsByKey(fin.rows, "revenue")} {...{ companyId, ticker, name, saved, onOpenChart, token, label }} />
              <Quality rows={fin.rows} {...{ companyId, ticker, name, saved, onOpenChart, token, label }} />
            </>
          );
        }}
      </Gate>
    </>
  );
}

interface Common {
  token: string;
  companyId: string;
  ticker: string;
  name: string;
  saved: Saved | null;
  onOpenChart?: (s: ChartState) => void;
  label: (id: string) => string;
}

function ChartActions({ chart, saved, onOpenChart }: { chart: ChartState; saved: Saved | null; onOpenChart?: (s: ChartState) => void }) {
  return (
    <div className="row wrap chart-actions">
      {onOpenChart && <button className="btn sm ghost" onClick={() => onOpenChart(chart)}>Open in Chart builder</button>}
      {saved && <SaveChart saved={saved} chart={chart} />}
    </div>
  );
}

function Axes({ rows, totals, view, ...c }: Common & { rows: SegmentRow[]; totals: Map<number, number>; view: View }) {
  const axes = axesOf(rows);
  if (!axes.length) {
    return (
      <p className="empty">
        No segment or geography revenue for {c.name} yet. It comes from the XBRL dimensions in each 10-Q and 10-K; companies
        with a single reportable segment and no geographic breakdown have none.
      </p>
    );
  }
  return (
    <>
      {axes.map((axis) => <AxisBlock key={axis} t={axisTable(rows, axis, totals)} view={view} {...c} />)}
      <div className="foot">
        Members missing a period show “—”, never zero. Beyond the {SHOWN} largest, members are folded into “Other”. Bars stack only
        when the members add up to reported revenue (within {pct(COVERAGE_TOLERANCE, 0)}) in every period; otherwise each
        member is a line.
      </div>
    </>
  );
}

function AxisBlock({ t, view, token, companyId, ticker, saved, onOpenChart, label }: Common & { t: AxisTable; view: View }) {
  const chart = useMemo(() => axisChart(t, companyId, ticker, view), [t, companyId, ticker, view]);
  const off = t.coverage.filter((x) => x !== null && Math.abs(x - 1) > COVERAGE_TOLERANCE);
  return (
    <div className="rev-axis">
      <h3>{AXIS_TITLES[t.axis]}</h3>
      <ChartView token={token} chart={chart} name={chart.title} label={label} hasTtm={everyMetric} />
      <ChartActions chart={chart} saved={saved} onOpenChart={onOpenChart} />
      <div className="tw">
        <table className="t">
          <thead>
            <tr>
              <th className="first">Member</th>
              {t.periods.map((p) => <th key={p.key} className="n">{p.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {t.rows.map((r) => (
              <tr key={r.members.join("|")}>
                <td className="first" title={r.members.join(", ")}>
                  {r.label}
                  {r.note && <span className="faint small"> · {r.note}</span>}
                </td>
                {r.values.map((v, i) => (
                  <td key={t.periods[i]!.key} className={v === null ? "n faint" : "n"} title={r.sources[i] ?? undefined}>
                    {v === null ? "—" : money(v, "USD")}
                  </td>
                ))}
              </tr>
            ))}
            <tr className="coverage">
              <td className="first faint">Sum vs reported revenue</td>
              {t.coverage.map((x, i) => (
                <td key={t.periods[i]!.key}
                  className={x === null ? "n faint" : Math.abs(x - 1) > COVERAGE_TOLERANCE ? "n warn" : "n faint"}>
                  {x === null ? "—" : pct(x, 0)}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
      {off.length > 0 && (
        <p className="small muted">
          In {off.length} of {t.coverage.length} periods these members don't add up to reported revenue: the breakdown
          {view === "ttm" ? " (TTM needs four quarters of a member)" : ""} names only part of it, or overlaps. Read it as
          {" "}reported, not as shares of the total.
        </p>
      )}
    </div>
  );
}

function Decomposition({ rows, totals, ...c }: Common & { rows: SegmentRow[]; totals: Map<number, number> }) {
  const axes = axesOf(rows);
  const [axis, setAxis] = useState<SegmentAxisKind | null>(null);
  const pick = axis && axes.includes(axis) ? axis : axes[0];
  const t = useMemo(() => (pick ? axisTable(rows, pick, totals) : null), [rows, pick, totals]);
  const d = useMemo(() => (t ? decompose(t, rows, totals) : null), [t, rows, totals]);
  const chart = useMemo(() => (t ? decompositionChart(t, c.companyId, c.ticker) : null), [t, c.companyId, c.ticker]);
  return (
    <section className="card">
      <div className="card-h wrap">
        <h2>Growth decomposition</h2>
        {axes.length > 1 && (
          <span className="seg" role="group" aria-label="Breakdown">
            {axes.map((a) => (
              <button key={a} className={a === pick ? "on" : undefined} aria-pressed={a === pick} onClick={() => setAxis(a)}>
                {a === "business_segment" ? "Segments" : a === "geography" ? "Geography" : "Products"}
              </button>
            ))}
          </span>
        )}
        <span className="sub">percentage points of TTM revenue growth, year over year</span>
      </div>
      {!t || !d || !chart || !d.periods.length ? (
        <p className="empty">No segment revenue for {c.name} to decompose.</p>
      ) : (
        <>
          <ChartView token={c.token} chart={chart} name={chart.title} label={c.label} hasTtm={everyMetric} />
          <ChartActions chart={chart} saved={c.saved} onOpenChart={c.onOpenChart} />
          <div className="tw">
            <table className="t decomposition">
              <thead>
                <tr>
                  <th className="first">Contribution</th>
                  {d.periods.map((p) => <th key={p.key} className="n">{p.label}</th>)}
                </tr>
              </thead>
              <tbody>
                {d.rows.map((r) => (
                  <tr key={r.members.join("|")}>
                    <td className="first">{r.label}</td>
                    {r.points.map((v, i) => (
                      <td key={d.periods[i]!.key} className={v === null ? "n faint" : v >= 0 ? "n pos" : "n neg"}>
                        {v === null ? "—" : points(v)}
                      </td>
                    ))}
                  </tr>
                ))}
                <tr className="unattributed">
                  <td className="first faint" title="Members missing a period, a breakdown that doesn't add up to revenue, rounding">
                    Not attributed
                  </td>
                  {d.unattributed.map((v, i) => <td key={d.periods[i]!.key} className="n faint">{v === null ? "—" : points(v)}</td>)}
                </tr>
                <tr className="total">
                  <td className="first"><b>Revenue growth</b></td>
                  {d.total.map((v, i) => <td key={d.periods[i]!.key} className="n"><b>{v === null ? "—" : change(v / 100)}</b></td>)}
                </tr>
              </tbody>
            </table>
          </div>
          <div className="foot">
            A member's contribution = its TTM revenue change over four quarters / the company's TTM revenue four quarters
            earlier, over members reporting both periods. They add up to revenue growth only when the members cover revenue;
            the rest is “Not attributed”.
          </div>
        </>
      )}
    </section>
  );
}

function Quality({ rows, ...c }: Common & { rows: FinancialRow[] }) {
  const q = useMemo(() => revenueQuality(rows), [rows]);
  const chart = useMemo(() => qualityChart(c.companyId, c.ticker), [c.companyId, c.ticker]);
  const flagged = q.filter((r) => r.flagged).length;
  return (
    <section className="card">
      <div className="card-h">
        <h2>Revenue quality: receivables vs revenue</h2>
        <span className="sub">receivables at quarter ends · revenue TTM</span>
      </div>
      {!q.length ? (
        <p className="empty">No receivables reported for {c.name} in these periods (common for banks, insurers and some retailers).</p>
      ) : (
        <>
          <ChartView token={c.token} chart={chart} name={chart.title} label={c.label} hasTtm={everyMetric} />
          <ChartActions chart={chart} saved={c.saved} onOpenChart={c.onOpenChart} />
          {flagged > 0 && (
            <p className="badge warn" role="status">
              ⚠ Receivables grew more than {QUALITY_FLAG_POINTS} points faster than revenue in {flagged} of the last {q.length} quarters
            </p>
          )}
          <div className="tw">
            <table className="t quality">
              <thead>
                <tr>
                  <th className="first">Quarter</th>
                  <th className="n">Revenue (TTM)</th>
                  <th className="n">Receivables</th>
                  <th className="n">DSO (days)</th>
                  <th className="n">Revenue YoY</th>
                  <th className="n">Receivables YoY</th>
                  <th className="n">Gap</th>
                </tr>
              </thead>
              <tbody>
                {q.map((r) => (
                  <tr key={r.label} className={r.flagged ? "flagged" : undefined}>
                    <td className="first">{r.label}</td>
                    <td className="n">{money(r.revenue, "USD")}</td>
                    <td className="n">{money(r.receivables, "USD")}</td>
                    <td className="n">{r.dso.toFixed(0)}</td>
                    <td className="n">{r.revenueGrowth === null ? "—" : change(r.revenueGrowth)}</td>
                    <td className="n">{r.receivablesGrowth === null ? "—" : change(r.receivablesGrowth)}</td>
                    <td className={r.flagged ? "n flag" : "n"} title={r.flagged ? `More than ${QUALITY_FLAG_POINTS} points faster than revenue` : undefined}>
                      {r.gap === null ? "—" : `${points(r.gap)}${r.flagged ? " ⚠" : ""}`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="foot">
            DSO = receivables at the quarter end / TTM revenue × 365. A quarter is flagged when receivables grew more than
            {" "}{QUALITY_FLAG_POINTS} percentage points faster than TTM revenue over the same four quarters: sales booked faster
            than cash comes in, or customers paying later. A flag is a reason to read the 10-Q's MD&A and receivables note,
            not a verdict.
          </div>
        </>
      )}
    </section>
  );
}

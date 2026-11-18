import { Fragment, useMemo, useRef, type ReactNode } from "react";

import { LayerChart } from "../chart/LayerChart";
import type { Source } from "../chart/series";
import { ErrorPanel, Skeleton } from "../components/States";
import { TableScroll } from "../components/TableScroll";
import { day, paren } from "../format";
import { go } from "../route";
import {
  amountUnit,
  builtFromQuarters,
  formatCell,
  modelColumns,
  modelLines,
  modelSeries,
  modelState,
  quartersLabel,
  remainderLabel,
  SCENARIO_LABELS,
  type CellValue,
  type Column,
  type Line,
  type ModelRun,
  type ModelState,
  type Projected,
  type Scenario,
} from "./model";
import { useModelRun, type RunState } from "./useModelRun";

/** The cases a table shows, in order: bear, base, bull when there are three. */
const ORDER: Scenario[] = ["bear", "base", "bull"];

/** A model drawn: history (solid) and every shown case's forecast (dashed; bear and bull
 * lighter, each in its own colour, as their labels in the table) on one chart, then ONE table with
 * every line and the cases side by side: history, the TTM and the current year's reported quarters
 * once per line, then a row per case for the forecast years. History points and figures open their
 * filing. Valuation lines have figures only where a price typed today means something: the
 * trailing twelve months and the forecasts (past years read n/a, PAST_YEAR says why). */
export function ModelView({
  run,
  historyYears,
  forecastYears,
  company,
  cases,
  preview = [],
  stale = [],
  selected,
}: {
  run: ModelRun;
  historyYears: number;
  forecastYears: number;
  /** the company's name or ticker */
  company: string;
  /** the cases to show (each must be in `run.cases`) */
  cases: Scenario[];
  /** cases not added yet, shown as they would start */
  preview?: Scenario[];
  /** cases whose numbers don't parse as typed: these are their last valid ones */
  stale?: Scenario[];
  /** the case being edited, marked in the table */
  selected?: Scenario;
}) {
  const shown = ORDER.filter((c) => cases.includes(c) && run.cases[c]);
  // revenue and operating income each on their own scale (Riley 36: a 4% margin's operating income
  // read as zero on a revenue axis)
  const revenue = modelSeries(run, historyYears, shown, "revenue");
  const income = modelSeries(run, historyYears, shown, "operating_income");
  const columns = useMemo(() => modelColumns(run, historyYears, forecastYears), [run, historyYears, forecastYears]);
  const lines = useMemo(() => modelLines(run), [run]);
  const svg = useRef<SVGSVGElement>(null);
  const svgIncome = useRef<SVGSVGElement>(null);
  const open = (src: Source) => {
    if (src.companyId) go({ view: "filing", companyId: src.companyId, accessionNo: src.accessionNo, section: null });
  };
  if (!run.history.some((y) => y.revenue !== null)) {
    return <p className="empty">No annual revenue for {company} in the data, so there's nothing to forecast from.</p>;
  }
  const first = run.cases.base?.years[0];
  const cur = run.current;
  const fromQuarters = builtFromQuarters(run);
  const groups = [...new Set(lines.map((l) => l.group))];
  const caseLabel = (c: Scenario) => `${SCENARIO_LABELS[c]}${preview.includes(c) ? " (preview)" : ""}${stale.includes(c) ? " ⚠" : ""}`;
  return (
    <div className="model-view">
      <div className="model-charts">
        <LayerChart
          series={revenue}
          title={`${company}: revenue`}
          footnote={[
            `Reported: SEC filings via Tributary, fiscal years (TTM at each fiscal Q4)${run.history.length ? `, through ${day(run.history.at(-1)!.period_end, true)}` : ""}.`,
            first
              ? `Dashed from FY${first.fy}: your assumptions, not data${fromQuarters ? `, after FY${cur!.fy}'s ${quartersLabel(cur!.quarters)} as reported` : ""}.${shown.length > 1 ? " Bear and bull lighter, each in its own colour." : ""}`
              : "No forecast: fix the assumptions.",
          ]}
          svgRef={svg}
          onOpen={open}
          notes={false}
        />
        <LayerChart
          series={income}
          title={`${company}: operating income`}
          footnote={["Adjusted for your one-off items, if any; dashed is the forecast, as beside it."]}
          svgRef={svgIncome}
          onOpen={open}
          notes={false}
        />
      </div>
      {stale.length > 0 && (
        <p className="small editor-warn" role="status">
          Some {stale.join(" and ")} numbers don't read as percentages yet: the chart and table show the last ones that did.
        </p>
      )}
      {/* the forecast in view first (Riley 86, 116: FY2019-21 showed and the forecast hid behind "8 columns") */}
      <TableScroll label="The model" atEnd resetKey={`${columns.length}`}>
        <table className="t model-table">
          <caption className="visually-hidden">
            {company}: every line by fiscal year; reported history, the trailing twelve months
            {cur?.quarters ? `, FY${cur.fy}'s reported quarters` : ""}, then {shown.map((c) => SCENARIO_LABELS[c].toLowerCase()).join(", ")} forecasts side by side
          </caption>
          <thead>
            <tr>
              <th className="first" scope="col">Line</th>
              {shown.length > 1 && <th scope="col" className="case-h" data-frozen="">Case</th>}
              {columns.map((c) => (
                <th key={c.key} scope="col" className={`n ${c.kind}`}>
                  {c.label}
                  {c.sub && <span className="col-sub">{c.sub}</span>}
                </th>
              ))}
            </tr>
          </thead>
          {groups.map((g) => (
            <tbody key={g}>
              <tr className="group-row">
                <th className="first" scope="rowgroup" colSpan={columns.length + (shown.length > 1 ? 2 : 1)}>
                  {g}
                  {g === "Valuation at your price" && run.valuation && (
                    <span className="group-sub">
                      {formatCell(run.valuation.price, "USD/shares", run.valuation.market_cap_currency ?? amountUnit(run))} on {day(run.valuation.date, true)}:
                      the trailing twelve months and your forecast years only. Past years read n/a: Tributary has no past prices.
                    </span>
                  )}
                </th>
              </tr>
              {lines.filter((l) => l.group === g).map((l) => (
                <Fragment key={l.key}>
                  {shown.map((c, k) => (
                    <tr key={`${l.key}-${c}`} className={`${k === 0 ? "line-start" : ""}${c === selected && shown.length > 1 ? " selected" : ""}`}>
                      {k === 0 && <th className="first" scope="row" rowSpan={shown.length}>{l.label}</th>}
                      {shown.length > 1 && <td className={`case ${c}`}>{caseLabel(c)}</td>}
                      {columns.map((col) => {
                        if (col.kind !== "forecast") {
                          return k === 0 ? <Fact key={col.key} run={run} line={l} col={col} rows={shown.length} /> : null;
                        }
                        const p = run.cases[c]?.years[col.index!];
                        return <Forecast key={col.key} line={l} p={p} c={c} i={col.index!} preview={preview.includes(c)} currency={amountUnit(run)} />;
                      })}
                    </tr>
                  ))}
                </Fragment>
              ))}
            </tbody>
          ))}
        </table>
      </TableScroll>
    </div>
  );
}

function Fact({ run, line, col, rows }: { run: ModelRun; line: Line; col: Column; rows: number }) {
  let v: CellValue = null;
  let title: string | undefined;
  if (col.kind === "history") {
    const i = run.history.findIndex((y) => y.fy === col.fy);
    const y = run.history[i];
    if (y) {
      v = line.history(y, i);
      const src = y.sources[sourceKey(line.key)];
      const note = line.noteOf?.(y);
      title = note ?? (src?.accession_no ? `${src.form} filed ${day(src.filed ?? "", true)}, ${src.accession_no}` : undefined);
      if (note) {
        return (
          <td className={`n ${col.kind} basis`} rowSpan={rows} title={note}>
            {paren(formatCell(v, line.unit, amountUnit(run)))}{v === null ? "" : "*"}
          </td>
        );
      }
    }
  } else if (col.kind === "ttm") {
    v = run.ttm && line.ttm ? line.ttm(run.ttm) : null;
    title = `Trailing twelve months to ${run.ttm?.period ?? ""}`;
  } else if (run.current) {
    v = line.ytd ? line.ytd(run.current) : null;
    title = `FY${run.current.fy} ${quartersLabel(run.current.quarters)} as reported${line.key === "growth" ? ", against the same quarters a year earlier" : ""}`;
  }
  return (
    <td className={`n ${col.kind}${v === "n/a" ? " past" : ""}`} rowSpan={rows}
      title={v === "n/a" ? line.pastYear : v === null ? line.blank ?? title : title}>
      {paren(formatCell(v, line.unit, amountUnit(run)))}
    </td>
  );
}

function Forecast({ line, p, c, i, preview, currency }: { line: Line; p: Projected | undefined; c: Scenario; i: number; preview: boolean; currency: string }) {
  if (!p) return <td className="n forecast">—</td>;
  const v = line.forecast(p, c, i);
  const split = p.actual && p.remainder && (line.key === "revenue" || line.key === "oi")
    ? `${quartersLabel(p.actual.quarters)} reported ${formatCell(line.key === "revenue" ? p.actual.revenue : p.actual.operating_income_adjusted, "USD", currency)} + ${remainderLabel(4 - p.remainder.quarters)} ${formatCell(line.key === "revenue" ? p.remainder.revenue : p.remainder.operating_income, "USD", currency)} forecast`
    : null;
  const defaulted = rateOf(line.key);
  const isDefault = defaulted && p.defaulted.includes(defaulted);
  const title = split ?? (isDefault ? "Tributary's default (you left this rate empty in your assumptions)"
    : preview ? "Not added yet: what this case would start as" : p.note ?? undefined);
  return (
    <td className={`n forecast${isDefault ? " default" : ""}`} title={v === null ? line.blank ?? title : title}>
      {paren(formatCell(v, line.unit, currency))}
    </td>
  );
}

/** The rate behind a line, when the line is one. */
function rateOf(key: string): Projected["defaulted"][number] | null {
  return key === "nonop" ? "nonOperating" : key === "tax" ? "taxRate" : key === "share_change" ? "shareChange" : key === "fcf_margin" ? "fcfMargin" : null;
}

/** Which source a line's history cell cites. */
function sourceKey(line: string): string {
  return {
    revenue: "revenue", growth: "revenue", organic: "revenue", oi: "operating_income", margin: "operating_income",
    oi_reported: "operating_income", margin_reported: "operating_income", nonop: "pretax_income", tax: "income_tax",
    ni: "net_income", eps: "eps_diluted", eps_reported: "eps_diluted", eps_growth: "eps_diluted",
    shares: "weighted_shares_diluted", share_change: "weighted_shares_diluted", fcf: "free_cash_flow",
    fcf_margin: "free_cash_flow", buybacks: "share_buybacks",
  }[line] ?? "revenue";
}

/** A saved model drawn in a thesis or report: its assumptions as saved, run now. */
export function ModelEmbed({ token, state, company }: { token: string; state: ModelState; company: string }) {
  const saved = useMemo(() => modelState(state), [state]);
  const { run, retry } = useModelRun(token, saved);
  const cases = ORDER.filter((c) => c === "base" || state.scenarios?.[c as "bear" | "bull"]);
  return <RunView run={run} company={company} onRetry={retry} render={(r) => (
    <ModelView run={r} historyYears={state.historyYears} forecastYears={state.assumptions.length} company={company} cases={cases} />
  )} />;
}

/** Loading, refused, older-API and failed states of a model run, the same everywhere. */
export function RunView({
  run,
  company,
  render,
  onRetry,
}: {
  run: RunState;
  company: string;
  render: (run: ModelRun) => ReactNode;
  onRetry?: () => void;
}) {
  if (run.state === "loading") return <Skeleton chart label={`Loading ${company}'s model`} />;
  if (run.state === "older") {
    return (
      <div className="empty">
        <p>The model is being updated on the server: it draws again in a minute.</p>
        {onRetry && <button className="btn sm" onClick={onRetry}>Try again</button>}
      </div>
    );
  }
  if (run.state === "error") {
    return run.denied ? (
      <p className="empty">Your plan doesn't include {company}'s financials, so this model can't be drawn.</p>
    ) : (
      <ErrorPanel what={`${company}'s model`} detail={run.message} onRetry={onRetry} />
    );
  }
  return <>{render(run.run)}</>;
}

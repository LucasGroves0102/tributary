import { useMemo, useRef, type ReactNode } from "react";

import { LayerChart } from "../chart/LayerChart";
import { formatValue } from "../chart/metrics";
import type { Source } from "../chart/series";
import { ErrorPanel, Skeleton } from "../components/States";
import { day } from "../format";
import { go } from "../route";
import { modelRows, modelSeries, project, SCENARIO_LABELS, type Assumption, type HistoryYear, type ModelState, type Scenario } from "./model";
import { useHistory, type History } from "./useHistory";
import { TableScroll } from "../components/TableScroll";

const pct = (v: number | null) => (v === null ? "—" : `${(v * 100).toFixed(1)}%`);
const usd = (v: number | null) => (v === null ? "—" : formatValue(v, "USD"));

/** A model drawn: history (solid) and forecast (dashed) on one chart, then the years as a table
 * with history and forecast rows labelled. History points open their filing. With bear / bull
 * cases, the chart adds them lighter beside the base forecast and the table shows `selected`. */
export function ModelView({
  history,
  historyYears,
  assumptions,
  company,
  asOf,
  scenarios,
  selected = "base",
  preview = false,
  stale = false,
}: {
  history: HistoryYear[];
  historyYears: number;
  /** the base case; null: the assumptions don't parse; the history is drawn alone */
  assumptions: Assumption[] | null;
  /** the company's name or ticker */
  company: string;
  asOf: string | null;
  /** bear / bull cases (null: one doesn't parse, left out) */
  scenarios?: ModelState["scenarios"] | Partial<Record<"bear" | "bull", Assumption[] | null>>;
  /** the case the table shows */
  selected?: Scenario;
  /** the selected bear / bull case isn't added yet: `scenarios` holds what it would start as */
  preview?: boolean;
  /** the selected case doesn't parse as typed: these are its last valid numbers */
  stale?: boolean;
}) {
  const shown = useMemo(() => history.slice(-historyYears), [history, historyYears]);
  const projection = useMemo(() => (assumptions ? project(history, assumptions) : []), [history, assumptions]);
  const bear = scenarios?.bear;
  const bull = scenarios?.bull;
  const others = useMemo(() => ({
    ...(bear ? { bear: project(history, bear) } : {}),
    ...(bull ? { bull: project(history, bull) } : {}),
  }), [history, bear, bull]);
  const series = useMemo(() => modelSeries(shown, projection, others), [shown, projection, others]);
  // the table shows the selected case, never another one in its place (#98, #156)
  const tableCase = selected === "base" ? projection : (others[selected] ?? []);
  const caseName = selected === "base" ? (others.bear || others.bull ? "Base case" : null) : `${SCENARIO_LABELS[selected]} case`;
  const tableLabel = caseName && preview ? `${caseName} (preview)` : caseName;
  const rows = useMemo(() => modelRows(shown, tableCase), [shown, tableCase]);
  const svg = useRef<SVGSVGElement>(null);
  const open = (src: Source) => {
    if (src.companyId) go({ view: "filing", companyId: src.companyId, accessionNo: src.accessionNo, section: null });
  };

  if (!shown.some((y) => y.revenue !== null)) {
    return <p className="empty">No annual revenue for {company} in the data, so there's nothing to forecast from.</p>;
  }
  const first = projection[0];
  return (
    <div className="model-view">
      <LayerChart
        series={series}
        title={`${company}: revenue and operating income`}
        footnote={[
          `History: SEC filings via Tributary, fiscal years (TTM at each fiscal Q4)${asOf ? `, as of ${day(asOf.slice(0, 10), true)}` : ""}.`,
          first
            ? `Forecast (dashed) from FY${first.fy}: your assumptions, not data.${preview ? ` The ${selected} case isn't added yet: drawn lighter as it would start.` : others.bear || others.bull ? " Bear and bull cases lighter." : ""}`
            : "No forecast: fix the assumptions.",
        ]}
        svgRef={svg}
        onOpen={open}
        notes={false}
      />
      {stale && (
        <p className="small editor-warn" role="status">
          Some {selected === "base" ? "base" : selected} numbers don't read as percentages yet: the chart and table show the last ones that did.
        </p>
      )}
      {!tableCase.length && (
        <p className="small muted" role="status">The {selected} case has no forecast to show until its numbers read as percentages.</p>
      )}
      <TableScroll label="The model">
        <table className="t model-table">
          <caption className="visually-hidden">
            {company}: revenue, growth, operating margin and operating income by fiscal year; reported history, then your
            {tableLabel ? ` ${tableLabel.toLowerCase()}` : " forecast"}
          </caption>
          <thead>
            <tr>
              <th className="first" scope="col">Fiscal year</th>
              <th scope="col">Basis</th>
              <th className="n" scope="col">Revenue</th>
              <th className="n" scope="col">Growth</th>
              <th className="n" scope="col">Op. margin</th>
              <th className="n" scope="col">Op. income</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.label} className={r.forecast ? "forecast" : undefined}>
                <th className="first" scope="row">{r.label}</th>
                <td>
                  {r.forecast ? (
                    <span className="tag e" title={r.note ?? (preview ? "Not added yet: what this case would start as" : "Your assumption")}>{tableLabel ?? "Forecast"}</span>
                  ) : (
                    <span className="tag k" title={r.revenueSource ? `${r.revenueSource.form} filed ${day(r.revenueSource.filed, true)}, ${r.revenueSource.accessionNo}` : undefined}>
                      Reported
                    </span>
                  )}
                </td>
                <td className="n">{usd(r.revenue)}</td>
                <td className="n">{pct(r.growth)}</td>
                <td className="n">{pct(r.margin)}</td>
                <td className="n">{usd(r.operatingIncome)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
    </div>
  );
}

/** A saved model drawn in a thesis: its assumptions as saved, the history read now. */
export function ModelEmbed({
  token,
  state,
  company,
}: {
  token: string;
  state: ModelState;
  company: string;
}) {
  const history = useHistory(token, state.companyId);
  return <HistoryState history={history} company={company} render={(years, asOf) => (
    <ModelView history={years} historyYears={state.historyYears} assumptions={state.assumptions} company={company} asOf={asOf}
      scenarios={state.scenarios} />
  )} />;
}

/** Loading, refused and failed states of a model's history, the same everywhere. */
export function HistoryState({
  history,
  company,
  render,
  onRetry,
}: {
  history: History;
  company: string;
  render: (years: HistoryYear[], asOf: string) => ReactNode;
  onRetry?: () => void;
}) {
  if (history.state === "loading") return <Skeleton chart label={`Loading ${company}'s history`} />;
  if (history.state === "error") {
    return history.denied ? (
      <p className="empty">Your plan doesn't include {company}'s financials, so this model can't be drawn.</p>
    ) : (
      <ErrorPanel what={`${company}'s history`} detail={history.message} onRetry={onRetry} />
    );
  }
  return <>{render(history.years, history.asOf)}</>;
}

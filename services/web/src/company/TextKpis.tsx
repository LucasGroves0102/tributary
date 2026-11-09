import { useMemo, useRef, useState } from "react";

import { textKpis } from "../api";
import { LayerChart } from "../chart/LayerChart";
import type { Source } from "../chart/series";
import { day } from "../format";
import { go, href } from "../route";
import type { TextKpiPoint, TextKpis, TextKpiSeries } from "../types";
import { Gate } from "./Tables";
import {
  conflictNote, excerpt, figure, FROM_TEXT, hasFigures, TEXT_KPI_ORDER, textKpiSeries, when, writtenIn,
} from "./textKpis";
import { useLoad } from "./useLoad";

/** Rows shown per figure before "Show all". */
const SHOWN = 6;

/** Backlog, orders and order growth as the company's filings state them in words (F8b): a chart
 * and, per figure, each value with the passage that states it (it opens the reader there). The
 * company page's Overview and the Numbers step. Words only: the API decides every value. */
export function TextKpisCard({ token, companyId, name, canRead, compact = false }: {
  token: string;
  companyId: string;
  name: string;
  /** the filings tier: these figures are read from filing text */
  canRead: boolean;
  /** the Numbers step: no chart heading of its own, fewer rows */
  compact?: boolean;
}) {
  const load = useLoad(canRead ? `${companyId}|text-kpis` : null, () => textKpis(token, companyId));
  return (
    <section className="card text-kpis" aria-label={`Backlog and orders ${FROM_TEXT}`}>
      <div className="card-h">
        <h2>Backlog and orders</h2>
        <span className="sub">{FROM_TEXT}: what the 10-K, 10-Q and earnings releases state in words, not XBRL · each figure links to its passage</span>
      </div>
      {!canRead ? (
        <p className="empty locked">These figures are read from {name}'s filing text, which your plan doesn't include.</p>
      ) : (
        <Gate load={load} name={name} what="Filings">
          {() => <Body data={load?.state === "ok" ? load.data : null} companyId={companyId} name={name} compact={compact} />}
        </Gate>
      )}
    </section>
  );
}

function Body({ data, companyId, name, compact }: { data: TextKpis | null; companyId: string; name: string; compact: boolean }) {
  const svg = useRef<SVGSVGElement>(null);
  const series = useMemo(() => (data ? textKpiSeries(data, companyId) : []), [data, companyId]);
  if (data === null) return <p className="empty">Backlog and orders from the filings' text aren't available here yet. Try again in a few minutes.</p>;
  const from = data.read.from ? day(data.read.from, true) : null;
  if (!hasFigures(data)) {
    return (
      <div className="card-b">
        <p className="empty text-kpis-none">
          {name}'s filings{from ? ` since ${from}` : ""} don't state a backlog or orders in the ways Tributary reads: a sentence such as
          “our backlog was $X billion at December 31” or “orders of $X billion in the quarter”, or a table's “Total backlog” row, in MD&A,
          the 10-K's Item 1 or an earnings release. A company can state them otherwise, or only by segment; the Filings tab has the full text.
        </p>
        <p className="foot">{data.read.filings} filings mention backlog or orders; none states them in these ways. {data.method}</p>
      </div>
    );
  }
  const note = conflictNote(data);
  const opened = (src: Source) => {
    const s = TEXT_KPI_ORDER.flatMap((m) => data.metrics[m].points).flatMap((p) => p.sources)
      .find((x) => x.citation.accession_no === src.accessionNo);
    go({ view: "filing", companyId, accessionNo: src.accessionNo, section: s?.citation.section_key ?? null,
      ...(s ? { at: s.citation.char_start } : {}) });
  };
  const points = series.reduce((n, s) => n + s.points.length, 0);
  return (
    <div className="card-b">
      {points >= 2 && (
        // sparse by nature (a figure when a filing states one): no gap notes under the chart
        <LayerChart series={series} title="" footnote={[]} svgRef={svg} onOpen={opened} notes={false} />
      )}
      {TEXT_KPI_ORDER.map((m) => data.metrics[m].points.length > 0 && (
        <Figures key={m} metric={m} series={data.metrics[m]} companyId={companyId} shown={compact ? 4 : SHOWN} />
      ))}
      <p className="foot">
        {from ? `Read from the ${data.read.filings} filings since ${from} that mention backlog or orders. ` : ""}
        {note ? `${note} ` : ""}{data.method}
      </p>
    </div>
  );
}

function Figures({ metric, series, companyId, shown }: { metric: string; series: TextKpiSeries; companyId: string; shown: number }) {
  const [all, setAll] = useState(false);
  const rows = all ? series.points : series.points.slice(0, shown);
  return (
    <div className="text-kpi" data-metric={metric}>
      <h3>{series.label} <span className="faint small">({FROM_TEXT})</span></h3>
      <table className="text-kpi-table">
        <thead>
          <tr><th scope="col">As of</th><th scope="col" className="num">Figure</th><th scope="col">Stated in</th></tr>
        </thead>
        <tbody>
          {rows.map((p) => <Row key={`${p.period_end}|${p.basis ?? ""}`} p={p} companyId={companyId} />)}
        </tbody>
      </table>
      {series.points.length > shown && (
        <button className="btn sm ghost" onClick={() => setAll(!all)}>
          {all ? "Show fewer" : `Show all ${series.points.length}`}
        </button>
      )}
    </div>
  );
}

function Row({ p, companyId }: { p: TextKpiPoint; companyId: string }) {
  const first = p.sources[0]!;
  const c = first.citation;
  const more = new Set(p.sources.map((s) => s.citation.accession_no).filter((a) => a !== c.accession_no));
  const exact = p.unit === "USD" ? `${p.value.toLocaleString("en-US")} US dollars` : `${p.value}%`;
  return (
    <tr>
      <td>{when(p)}{p.basis ? <span className="faint small"> · {p.basis === "organic" ? "organic" : "as reported"}</span> : null}</td>
      <td className="num" title={exact}>{figure(p)}</td>
      <td>
        <a href={href({ view: "filing", companyId, accessionNo: c.accession_no, section: c.section_key, at: c.char_start })}>
          {writtenIn(first)}
        </a>
        {more.size > 0 && <span className="faint small"> · also in {more.size} more filing{more.size === 1 ? "" : "s"}</span>}
        <q className="passage">{excerpt(first)}</q>
      </td>
    </tr>
  );
}

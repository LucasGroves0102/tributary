import { Fragment, useMemo, useState, type ReactNode } from "react";

import { peers as fetchPeers } from "../api";
import { MAX_COMPANY_LINES } from "../chart/model";
import {
  COMPARE_COLUMNS,
  compareRows,
  countText,
  MASKED_TITLE,
  medianTitle,
  nextSort,
  peerStats,
  sortRows,
  statText,
  type CompareSort,
} from "../compare/state";
import { fallbackPart, fallbackSentence, possessive, similarityWord, whyParts, whyText } from "../compare/peers";
import { FigureText } from "../components/FigureText";
import { NOT_APPLICABLE } from "../blanks";
import { day } from "../format";
import { go, href } from "../route";
import { useScope } from "../scope";
import { Gate } from "./Tables";
import { useLoad } from "./useLoad";
import { TableScroll } from "../components/TableScroll";

/** What the table shows of each peer when the API answers with columns this app doesn't know. */
const KNOWN = new Set(COMPARE_COLUMNS.map((c) => c.key));

/** Suggested peers (by what they do, F4: the API decides and says why), into Compare or the chart
 * builder. Columns are the API's: a bank's own for a bank. */
export function Peers({
  token,
  companyId,
  name,
  onChart,
}: {
  token: string;
  companyId: string;
  name: string;
  onChart: (ids: string[], metric: string, transform: "ttm") => void;
}) {
  // a demo sandbox ranks only its own companies (scope.ts)
  const scoped = useScope() !== null;
  const load = useLoad(companyId, () => fetchPeers(token, companyId, undefined, 10, scoped));
  const data = load?.state === "ok" ? load.data : null;
  // the answer's columns (a bank's own for a bank); else the keys its rows carry, in table order
  const cols = data
    ? data.columns.length
      ? data.columns.map((c) => c.key).filter((k) => KNOWN.has(k))
      : COMPARE_COLUMNS.map((c) => c.key).filter((k) => [data.company, ...data.peers].some((r) => k in r.values))
    : [];
  const peerIds = data?.peers.map((p) => p.company_id) ?? [];
  const byId = new Map(data?.peers.map((p) => [p.company_id, p]) ?? []);
  const rows = useMemo(
    () => (data ? compareRows({ target: companyId, peers: peerIds, cols }, [data.company, ...data.peers]) : []),
    // the answer decides everything here
    [data],
  );
  const stats = useMemo(() => peerStats(rows, cols), [rows, cols.join()]);
  // the suggested order until a header is clicked, as on Compare
  const [sort, setSort] = useState<CompareSort | null>(null);
  const sorted = useMemo(() => sortRows(rows, sort), [rows, sort]);
  const sortHead = (key: string, content: ReactNode, title?: string, unit?: string) => (
    <button type="button" className="th-sort" title={title} onClick={() => setSort((s) => nextSort(s, key))}>
      {content}
      {sort?.key === key ? (sort.desc ? " ↓" : " ↑") : ""}
      {unit && <span className="unit"> {unit}</span>}
    </button>
  );
  const ariaSort = (key: string) => (sort?.key === key ? (sort.desc ? ("descending" as const) : ("ascending" as const)) : undefined);
  const readable = rows.filter((r) => r.readable).map((r) => r.companyId);
  const capped = readable.length > MAX_COMPANY_LINES;
  // a bank peer group has no operating margin to chart; net margin is what they all report
  const chartNet = rows.length > 0 && rows.every((r) => !r.readable || r.cells.operating_margin_ttm?.value == null);
  const business = data?.basis === "business";
  const target = { companyId, name };
  // by SIC and size: every row's Why starts by saying so, and why (the API's basis_reason)
  const fallback = data ? fallbackPart(data, name) : null;
  const fallbackLine = data ? fallbackSentence(data, name) : null;

  return (
    <section className="card">
      <div className="card-h wrap">
        <h2>Peers</h2>
        <span className="sub" title={data?.method}>
          {!data ? "by what they do" : business ? "by what they do: their 10-K Business sections, competitors named, then size" : "by SIC code and size"}
        </span>
        <span className="grow" />
        <button className="btn sm" disabled={!peerIds.length} onClick={() => go({ view: "compare", compare: { target: companyId, peers: peerIds } })}>
          Open in Compare
        </button>
        <button className="btn sm" disabled={readable.length < 2}
          onClick={() => onChart(readable, chartNet ? "net_margin" : "operating_margin", "ttm")}
          title={`${chartNet ? `Net margin, TTM: ${rows.some((r) => r.cells.operating_margin_ttm?.status === "not_applicable") || !cols.includes("operating_margin_ttm") ? "operating margin doesn't describe a bank" : "too few of these report an operating margin"}, so this` : "Operating margin, TTM:"} one line per company you can read${capped ? `, the first ${MAX_COMPANY_LINES} (a chart tells ${MAX_COMPANY_LINES} colours apart)` : ""}`}>
          Chart {chartNet ? "net " : ""}margins{capped ? ` (first ${MAX_COMPANY_LINES} of ${readable.length})` : ""}
        </button>
      </div>
      <Gate load={load} name={name} what="Peers">
        {() =>
          !peerIds.length && scoped ? (
            <p className="empty">
              None of the other companies in your demo share {name}'s industry. Compare it with
              any of them in <a href={href({ view: "compare", compare: { target: companyId } })}>Compare</a>.
            </p>
          ) : !peerIds.length ? (
            <p className="empty">
              No suggestions for {name}: {data?.method ?? ""} Add peers by hand in{" "}
              <a href={href({ view: "compare", compare: { target: companyId } })}>Compare</a>.
            </p>
          ) : (
            <>
              {business && data?.business && (
                <p className="peers-basis small">
                  <span className="faint">{possessive(name)} 10-K Business section (filed {day(data.business.filed, true)}) is most distinctive for </span>
                  {data.business.terms.slice(0, 6).join(", ")}
                  <span className="faint">. Peers are the companies whose 10-Ks read most alike, or that either names as a competitor.</span>
                </p>
              )}
              {fallbackLine && <p className="peers-basis small faint">{fallbackLine}</p>}
              <TableScroll label="Peers">
                <table className="t peers">
                  <thead>
                    <tr>
                      <th className="first" aria-sort={ariaSort("name")}>{sortHead("name", "Company")}</th>
                      {cols.map((k) => {
                        const c = COMPARE_COLUMNS.find((x) => x.key === k)!;
                        return (
                          <th key={k} className="n" aria-sort={ariaSort(k)}>
                            {sortHead(k, c.label, c.long, c.period)}
                          </th>
                        );
                      })}
                      <th>FYE</th>
                      <th className="why">Why</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sorted.map((r) => {
                      const p = byId.get(r.companyId);
                      const reasons = r.isTarget || !p ? [] : [...(fallback ? [fallback] : []), ...whyParts(p.match, p, target)];
                      const alike = similarityWord(p?.match.similarity);
                      const why = r.isTarget ? "this company" : whyText(reasons);
                      return (
                        <tr key={r.companyId} className={[r.isTarget ? "sel" : "", r.readable ? "" : "masked"].join(" ").trim() || undefined}>
                          <td className="first">
                            <a className="co" href={href({ view: "company", companyId: r.companyId })}>
                              <b>{r.name}</b>
                              {r.ticker && <span className="faint mono">{r.ticker}</span>}
                              {!r.readable && <span className="tag" title={MASKED_TITLE}>metadata only</span>}
                            </a>
                            {why && <div className="co-sub faint small" title={why}>{alike ? `${alike} · ` : ""}{why}</div>}
                          </td>
                          {cols.map((k) => {
                            const cell = r.cells[k]!;
                            const tone = cell.value === null ? "faint" : k === "revenue_growth_ttm" ? (cell.value >= 0 ? "pos" : "neg") : "";
                            return (
                              <td key={k} className={`n ${tone}`} title={cell.title}>
                                <FigureText cell={cell} />
                              </td>
                            );
                          })}
                          <td className="muted" title={r.fye ?? undefined}>{r.fye?.slice(0, 3) ?? "—"}</td>
                          <td className="muted why">
                            {r.isTarget ? "this company" : (
                              <>
                                {alike && <span className="alike" title={`Business sections: cosine similarity ${p?.match.similarity?.toFixed(2)} (0 none, 1 the same words)`}>{alike}</span>}
                                {reasons.map((x, i) => (
                                  <Fragment key={i}>
                                    {(i > 0 || alike) && <span className="faint"> · </span>}
                                    {x.cite ? (
                                      <a href={href({ view: "filing", companyId: x.cite.companyId, accessionNo: x.cite.mention.accession_no, section: x.cite.mention.section_key, at: x.cite.mention.char_start })}
                                        title="Open the sentence in the filing reader">
                                        {x.text}
                                      </a>
                                    ) : x.title ? <span className="why-basis" title={x.title}>{x.text}</span> : x.text}
                                  </Fragment>
                                ))}
                              </>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                    <tr className="agg">
                      <td className="first" title="Each median says how many of the peers it uses; hover it for why the others are left out">Peer median <span className="unit">n of N</span></td>
                      {cols.map((k) => (
                        <td key={k} className="n" title={medianTitle(stats[k]!)}>
                          {stats[k]!.n === 0 && stats[k]!.banks > 0 ? NOT_APPLICABLE : statText(k, stats[k]!.median)}
                          <span className="unit count">{countText(stats[k]!)}</span>
                        </td>
                      ))}
                      <td />
                      <td />
                    </tr>
                  </tbody>
                </table>
              </TableScroll>
            </>
          )
        }
      </Gate>
      <div className="foot">
        {business
          ? "Similar businesses come from each company's latest 10-K Business section (Item 1), compared word by word; a company its 10-K names as a competitor ranks up, and a link opens that sentence. SIC division and revenue size count a little. "
          : "Same SIC code first, then revenue size. "}
        Sizes use TTM revenue, only for companies whose financials you can read. Each median says how
        many of the peers it uses (n of N); hover it for why the others are left out (— not
        reported, n/a, n/m or old). Hover a value for its period and source filing. Click a column to
        sort (again to reverse, a third time for the suggested order).
      </div>
    </section>
  );
}

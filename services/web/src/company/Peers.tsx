import { peers as fetchPeers } from "../api";
import { COMPARE_COLUMNS, compareRows, MASKED_TITLE } from "../compare/state";
import { go, href } from "../route";
import { Gate } from "./Tables";
import { useLoad } from "./useLoad";

export const PEER_COLUMNS = ["revenue_ttm", "revenue_growth_ttm", "gross_margin_ttm", "operating_margin_ttm", "free_cash_flow_ttm", "net_debt_q"];
const SIC_LEVEL: Record<number, string> = { 4: "same SIC", 3: "same 3-digit SIC group", 2: "same 2-digit SIC group" };

/** Suggested peers (same SIC first, then size), into Compare or the chart builder. */
export function Peers({
  token,
  companyId,
  name,
  onChart,
}: {
  token: string;
  companyId: string;
  name: string;
  onChart: (ids: string[], metric: string, transform: "ttm", title: string) => void;
}) {
  const load = useLoad(companyId, () => fetchPeers(token, companyId, PEER_COLUMNS, 10));
  const data = load?.state === "ok" ? load.data : null;
  const peerIds = data?.peers.map((p) => p.company_id) ?? [];
  const match = new Map(data?.peers.map((p) => [p.company_id, p.match]) ?? []);
  const state = { target: companyId, peers: peerIds, cols: PEER_COLUMNS };
  const rows = data ? compareRows(state, [data.company, ...data.peers]) : [];
  const readable = rows.filter((r) => r.readable).map((r) => r.companyId);

  return (
    <section className="card">
      <div className="card-h wrap">
        <h2>Peers</h2>
        <span className="sub">{data ? data.method : "same SIC, then size"}</span>
        <span className="grow" />
        <button className="btn sm" disabled={!peerIds.length} onClick={() => go({ view: "compare", compare: { target: companyId, peers: peerIds } })}>
          Open in Compare
        </button>
        <button className="btn sm" disabled={readable.length < 2}
          onClick={() => onChart(readable, "operating_margin", "ttm", "Operating margin, TTM")}
          title="Operating margin, TTM: one line per company you can read">
          Chart margins
        </button>
      </div>
      <Gate load={load} name={name} what="Peers">
        {() =>
          !peerIds.length ? (
            <p className="empty">
              No companies share {name}'s SIC code or group. Add peers by hand in{" "}
              <a href={href({ view: "compare", compare: { target: companyId } })}>Compare</a>.
            </p>
          ) : (
            <div className="tw">
              <table className="t">
                <thead>
                  <tr>
                    <th className="first">Company</th>
                    {PEER_COLUMNS.map((k) => {
                      const c = COMPARE_COLUMNS.find((x) => x.key === k)!;
                      return (
                        <th key={k} className="n">
                          {c.label} <span className="unit">{c.period}</span>
                        </th>
                      );
                    })}
                    <th>FYE</th>
                    <th>Why</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => {
                    const m = match.get(r.companyId);
                    return (
                      <tr key={r.companyId} className={[r.isTarget ? "sel" : "", r.readable ? "" : "masked"].join(" ").trim() || undefined}>
                        <td className="first">
                          <a className="co" href={href({ view: "company", companyId: r.companyId })}>
                            <b>{r.name}</b>
                            {r.ticker && <span className="faint mono">{r.ticker}</span>}
                            {!r.readable && <span className="tag" title={MASKED_TITLE}>metadata only</span>}
                          </a>
                        </td>
                        {PEER_COLUMNS.map((k) => {
                          const cell = r.cells[k]!;
                          return (
                            <td key={k} className={cell.value === null ? "n faint" : "n"} title={cell.title}>
                              {cell.text}
                            </td>
                          );
                        })}
                        <td className="muted">{r.fye ?? "—"}</td>
                        <td className="muted">
                          {r.isTarget
                            ? "this company"
                            : m
                              ? [SIC_LEVEL[m.sic_level] ?? "SIC", m.same_revenue_band && "same size band", m.same_filer_category && "same filer category"]
                                  .filter(Boolean)
                                  .join(", ")
                              : ""}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )
        }
      </Gate>
      <div className="foot">
        Size bands use TTM revenue, only for companies whose financials you can read. Hover a
        value for its period and source filing.
      </div>
    </section>
  );
}

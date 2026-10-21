import { useEffect, useMemo, useState } from "react";

import { ApiError, filings as fetchFilings, financials, searchCompanies } from "../../api";
import { ago, METRIC_LABELS, money, quarter } from "../../format";
import { href } from "../../route";
import type { Filing, FinancialRow, SearchResult, Tier } from "../../types";
import { Chart } from "./Chart";

type Load<T> = { state: "loading" } | { state: "ok"; data: T } | { state: "denied" | "error" };

const QUARTERS = 12;
const TABLE_QUARTERS = 8;

function load<T>(p: Promise<T>, set: (l: Load<T>) => void, live: () => boolean) {
  p.then((data) => live() && set({ state: "ok", data })).catch(
    (e) => live() && set({ state: e instanceof ApiError && e.status === 403 ? "denied" : "error" }),
  );
}

export function CompanyPage({
  token,
  companyId,
  onAsk,
}: {
  token: string;
  companyId: string;
  onAsk: (question: string) => void;
}) {
  const [identity, setIdentity] = useState<SearchResult | null>(null);
  const [fin, setFin] = useState<Load<{ as_of: string; rows: FinancialRow[] }>>({ state: "loading" });
  const [docs, setDocs] = useState<Load<Filing[]>>({ state: "loading" });
  const [metric, setMetric] = useState("revenue");

  useEffect(() => {
    let live = true;
    setIdentity(null);
    setFin({ state: "loading" });
    setDocs({ state: "loading" });
    searchCompanies(token, companyId)
      .then((r) => live && setIdentity(r.find((c) => c.company_id === companyId) ?? null))
      .catch(() => {});
    load(financials(token, companyId, QUARTERS), setFin, () => live);
    load(fetchFilings(token, companyId), setDocs, () => live);
    return () => {
      live = false;
    };
  }, [token, companyId]);

  const byMetric = useMemo(() => {
    const out = new Map<string, FinancialRow[]>();
    if (fin.state === "ok") {
      for (const r of fin.data.rows) out.set(r.metric, [...(out.get(r.metric) ?? []), r]);
    }
    return out;
  }, [fin]);

  const name = identity?.name ?? companyId;
  const access: Tier[] = identity?.access ?? [];
  const latestFiled =
    fin.state === "ok" ? fin.data.rows.map((r) => r.citation.filed).sort().at(-1) : undefined;

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{name}</h1>
          <p className="muted">
            CIK {companyId}
            {identity && identity.matched_on !== "cik" ? ` · ${identity.matched_alias}` : ""}
          </p>
        </div>
        <div className="header-side">
          <span className="tiers">
            {access.length ? (
              access.map((t) => (
                <span key={t} className={`tier ${t}`}>
                  {t}
                </span>
              ))
            ) : (
              <span className="tier none">no access</span>
            )}
          </span>
          {access.length > 0 && (
            <button className="ask-about" onClick={() => onAsk(`What stands out in ${name}'s latest results?`)}>
              Ask about {name}
            </button>
          )}
        </div>
      </header>

      <section className="card pad">
        <div className="card-head">
          <h2>Quarterly financials</h2>
          {fin.state === "ok" && (
            <span className="muted small">
              Latest filing {latestFiled ?? "—"} · as of {ago(fin.data.as_of)}
            </span>
          )}
        </div>
        {fin.state === "loading" && <p className="muted">Loading…</p>}
        {fin.state === "denied" && (
          <p className="locked">Financials for {name} aren't included in your access.</p>
        )}
        {fin.state === "error" && <p className="error">Couldn't load financials.</p>}
        {fin.state === "ok" && (
          <>
            <div className="metric-tabs" role="tablist" aria-label="Metric">
              {Object.entries(METRIC_LABELS)
                .filter(([m]) => byMetric.has(m))
                .map(([m, label]) => (
                  <button
                    key={m}
                    role="tab"
                    aria-selected={m === metric}
                    className={m === metric ? "metric-tab active" : "metric-tab"}
                    onClick={() => setMetric(m)}
                  >
                    {label}
                  </button>
                ))}
            </div>
            <Chart rows={byMetric.get(metric) ?? []} label={METRIC_LABELS[metric] ?? metric} />
            <FinancialsTable byMetric={byMetric} />
            <p className="muted small">
              Every value links to the filing it came from. Derived values (lighter bars) are
              computed from year-to-date figures.
            </p>
          </>
        )}
      </section>

      <section className="card pad">
        <div className="card-head">
          <h2>Filings</h2>
        </div>
        {docs.state === "loading" && <p className="muted">Loading…</p>}
        {docs.state === "denied" && (
          <p className="locked">Filing text needs the pro plan and a grant for {name}.</p>
        )}
        {docs.state === "error" && <p className="error">Couldn't load filings.</p>}
        {docs.state === "ok" &&
          (docs.data.length === 0 ? (
            <p className="muted">No parsed filings yet.</p>
          ) : (
            <ul className="filing-list">
              {docs.data.map((f) => (
                <li key={f.accession_no}>
                  <a href={href({ view: "filing", companyId, accessionNo: f.accession_no, section: null })}>
                    <span className="form">{f.form}</span>
                    <span>filed {f.filed}</span>
                    {f.period_of_report && <span className="muted">period {f.period_of_report}</span>}
                  </a>
                  <span className="muted small">
                    {f.sections.length} section{f.sections.length === 1 ? "" : "s"}
                  </span>
                </li>
              ))}
            </ul>
          ))}
      </section>
    </div>
  );
}

function FinancialsTable({ byMetric }: { byMetric: Map<string, FinancialRow[]> }) {
  const periods = [...new Set([...byMetric.values()].flat().map((r) => r.period_end))]
    .sort()
    .reverse()
    .slice(0, TABLE_QUARTERS);
  return (
    <div className="table-scroll">
      <table className="financials">
        <thead>
          <tr>
            <th scope="col">Metric</th>
            {periods.map((p) => (
              <th key={p} scope="col">
                {quarter(p)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Object.entries(METRIC_LABELS)
            .filter(([m]) => byMetric.has(m))
            .map(([m, label]) => {
              const cells = new Map(byMetric.get(m)!.map((r) => [r.period_end, r]));
              return (
                <tr key={m}>
                  <th scope="row">{label}</th>
                  {periods.map((p) => {
                    const r = cells.get(p);
                    if (!r) return <td key={p} className="muted">—</td>;
                    const text = money(Number(r.value), r.unit);
                    const title = `${r.citation.form} filed ${r.citation.filed} · ${r.citation.xbrl_tag} · ${r.value_source}`;
                    return (
                      <td key={p} className={r.value_source}>
                        {r.citation.filing_url ? (
                          <a href={r.citation.filing_url} target="_blank" rel="noopener noreferrer" title={title}>
                            {text}
                          </a>
                        ) : (
                          <span title={title}>{text}</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
        </tbody>
      </table>
    </div>
  );
}

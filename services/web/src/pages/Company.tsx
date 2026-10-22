import { expectedFilingsOf, screener } from "../api";
import { header, HEADER_COLUMNS } from "../company/data";
import { Filings } from "../company/Filings";
import { Overview } from "../company/Overview";
import { Peers } from "../company/Peers";
import { Ratios, Segments, Statements } from "../company/Tables";
import { useLoad } from "../company/useLoad";
import { day } from "../format";
import { COMPANY_TABS, go, href, type CompanyTab } from "../route";

interface Props {
  token: string;
  companyId: string;
  tab: CompanyTab;
  /** null while the watchlist loads or is unavailable */
  watched: boolean | null;
  onWatch: (watch: boolean) => Promise<void>;
  onChart: (name: string) => void;
  onChartMany: (ids: string[], metric: string, transform: "ttm", title: string) => void;
  onAsk: (question: string) => void;
}

/** The tearsheet: who the company is (public metadata), then its data by tab. Identity, the
 * filing index and expected dates are public; values need financials, text needs filings. */
export function Company({ token, companyId, tab, watched, onWatch, onChart, onChartMany, onAsk }: Props) {
  const meta = useLoad(companyId, () =>
    screener(token, { company_id: [companyId], columns: HEADER_COLUMNS, sort: "name", limit: 1 }).then(
      (r) => r.rows.find((x) => x.company_id === companyId) ?? null,
    ),
  );
  const expected = useLoad(companyId, () => expectedFilingsOf(token, companyId));

  if (!meta || meta.state === "loading") return <div className="page"><p className="muted">Loading…</p></div>;
  if (meta.state !== "ok" || meta.data === null) {
    return (
      <div className="page">
        <p className="empty error">{meta.state === "ok" ? `No company ${companyId}.` : "Couldn't load this company."}</p>
      </div>
    );
  }
  const row = meta.data;
  const h = header(row);
  const ticker = h.tickers[0] ?? companyId;
  const next = expected?.state === "ok" ? (expected.data.expected[0] ?? null) : null;

  return (
    <div className="page">
      <section className="card">
        <div className="card-b co-head">
          <div className="grow">
            <div className="row wrap">
              <h1>{h.name}</h1>
              <span className="mono muted">{[h.tickers.join(", "), h.exchanges.join(", ")].filter(Boolean).join(" · ")}</span>
              <span className="tiers">
                {row.access.length ? (
                  row.access.map((t) => <span key={t} className={`tier ${t}`}>{t}</span>)
                ) : (
                  <span className="tier none">metadata only</span>
                )}
              </span>
            </div>
            <div className="meta">
              {[
                h.industry && `${h.industry}${h.sic ? ` (SIC ${h.sic})` : ""}`,
                h.fye && `fiscal year ends ${h.fye}`,
                h.filer && `${h.filer.toLowerCase()} filer`,
                h.state && `incorporated in ${h.state}`,
                `CIK ${companyId}`,
              ].filter(Boolean).join(" · ")}
            </div>
            <div className="meta">
              {next?.last_filed && <>last periodic report filed {day(next.last_filed, true)} · </>}
              {next ? (
                <span title={expected?.state === "ok" ? expected.data.method : undefined}>
                  next {next.form} for the period ending {day(next.period_end, true)} expected
                  {next.expected_date ? ` ~${day(next.expected_date, true)}` : ""}
                  <span className="faint"> (estimate{next.status !== "upcoming" ? `, ${next.status}` : ""})</span>
                </span>
              ) : (
                <span className="faint">no expected filing date</span>
              )}
            </div>
          </div>
          <div className="row wrap">
            {watched !== null && (
              <button className="btn" onClick={() => void onWatch(!watched)}
                title={row.access.includes("financials") ? undefined : "Watching follows its filings; it grants no access to values"}>
                {watched ? "✓ Watching" : "+ Watch"}
              </button>
            )}
            {row.access.includes("financials") && (
              <button className="btn" onClick={() => onChart(h.name)}>Chart</button>
            )}
            <button className="btn" onClick={() => go({ view: "compare", compare: { target: companyId } })}>Compare</button>
            <button className="btn" onClick={() => onAsk(`Summarize ${h.name}'s latest quarter.`)}>Ask</button>
          </div>
        </div>
        <nav className="tabs" aria-label="Company sections">
          {COMPANY_TABS.map(([t, label]) => (
            <a key={t} href={href({ view: "company", companyId, tab: t })} aria-current={t === tab ? "page" : undefined}>
              {label}
            </a>
          ))}
        </nav>
      </section>

      {tab === "overview" && <Overview token={token} companyId={companyId} name={h.name} row={row} access={row.access} />}
      {tab === "statements" && <Statements token={token} companyId={companyId} name={h.name} ticker={ticker} />}
      {tab === "segments" && <Segments token={token} companyId={companyId} name={h.name} />}
      {tab === "ratios" && <Ratios token={token} companyId={companyId} name={h.name} ticker={ticker} />}
      {tab === "filings" && <Filings token={token} companyId={companyId} name={h.name} canRead={row.access.includes("filings")} />}
      {tab === "peers" && <Peers token={token} companyId={companyId} name={h.name} onChart={onChartMany} />}
    </div>
  );
}

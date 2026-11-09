import { expectedFilingsOf, screener } from "../api";
import { header, HEADER_COLUMNS, listingLine } from "../company/data";
import { DataNotes } from "../company/DataNotes";
import { EarningsView } from "../company/Earnings";
import { Filings } from "../company/Filings";
import { Overview } from "../company/Overview";
import { Peers } from "../company/Peers";
import { Price } from "../company/Price";
import { useMarketData } from "../market/useMarketData";
import { Revenue } from "../company/Revenue";
import type { ChartState } from "../chart/model";
import type { StatementView } from "../company/statementView";
import { Ratios, Segments, Statements } from "../company/Tables";
import { useLoad } from "../company/useLoad";
import { ErrorPanel, Skeleton } from "../components/States";
import { day } from "../format";
import { COMPANY_TABS, go, href, type CompanyTab } from "../route";
import { NewWork } from "../workspaces/NewWork";
import type { Saved } from "../workspaces/useSaved";
import { restoreProject } from "../analysis/project";

interface Props {
  token: string;
  companyId: string;
  tab: CompanyTab;
  /** the Statements tab's choices from the address */
  statements?: StatementView;
  /** null while the watchlist loads or is unavailable */
  watched: boolean | null;
  onWatch: (watch: boolean) => Promise<void>;
  onChart: () => void;
  onChartMany: (ids: string[], metric: string, transform: "ttm") => void;
  /** a Revenue view chart opened in the chart builder */
  onOpenChart?: (s: ChartState) => void;
  onAsk: (question: string) => void;
  /** saved work, for "New thesis" / "New model" about this company (null: unavailable) */
  saved?: Saved | null;
}

/** The tearsheet: who the company is (public metadata), then its data by tab. Identity, the
 * filing index and expected dates are public; values need financials, text needs filings. */
export function Company({ token, companyId, tab, statements, watched, onWatch, onChart, onChartMany, onOpenChart, onAsk, saved }: Props) {
  const meta = useLoad(companyId, () =>
    screener(token, { company_id: [companyId], columns: HEADER_COLUMNS, sort: "name", limit: 1 }).then(
      (r) => r.rows.find((x) => x.company_id === companyId) ?? null,
    ),
  );
  const expected = useLoad(companyId, () => expectedFilingsOf(token, companyId));
  // the Price tab exists only when the server offers "Connect your data"
  const market = useMarketData(token);
  const tabs = COMPANY_TABS.filter(([t]) => t !== "price" || market?.enabled);

  if (!meta || meta.state === "loading") return <div className="page"><Skeleton lines={6} label="Loading the company" /></div>;
  if (meta.state === "error") {
    return (
      <div className="page">
        <ErrorPanel what="this company" detail={meta.message} onRetry={meta.retry} />
      </div>
    );
  }
  if (meta.state !== "ok" || meta.data === null) {
    return (
      <div className="page">
        <p className="empty">
          This company has no financials or filings here. Find one under{" "}
          <a href={href({ view: "companies" })}>Companies</a>, or search with Ctrl K.
        </p>
      </div>
    );
  }
  const row = meta.data;
  const h = header(row);
  // charts and saved work call it this: its ticker, or its name when it has none
  const ticker = h.tickers[0] ?? h.name;
  const next = expected?.state === "ok" ? (expected.data.expected[0] ?? null) : null;
  const listing = listingLine(h.tickers, h.exchanges);

  return (
    <div className="page">
      <section className="card">
        <div className="card-b co-head">
          <div className="grow">
            <div className="row wrap">
              <h1>{h.name}</h1>
              <span className="mono muted" title={listing.more ?? undefined}>{listing.text}</span>
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
              <button className="btn" onClick={() => onChart()}>Chart</button>
            )}
            <button className="btn" onClick={() => go({ view: "compare", compare: { target: companyId } })}>Compare</button>
            <button className="btn" onClick={() => onAsk(`Summarize ${h.name}'s latest quarter.`)}>Ask</button>
            {saved && row.access.includes("financials") && <AnalysisLink saved={saved} companyId={companyId} ticker={ticker} />}
            {saved && <NewWork token={token} saved={saved} company={{ id: companyId, name: ticker }} />}
          </div>
        </div>
        <nav className="tabs" aria-label="Company sections">
          {tabs.map(([t, label]) => (
            <a key={t} href={href({ view: "company", companyId, tab: t })} aria-current={t === tab ? "page" : undefined}>
              {label}
            </a>
          ))}
        </nav>
      </section>

      {tab === "overview" && <Overview token={token} companyId={companyId} name={h.name} row={row} access={row.access} />}
      {tab === "earnings" && (
        <EarningsView token={token} companyId={companyId} name={h.name} canReadFilings={row.access.includes("filings")}
          canReadFinancials={row.access.includes("financials")} />
      )}
      {tab === "statements" && <Statements token={token} companyId={companyId} name={h.name} ticker={ticker} notApplicable={row.not_applicable} view={statements} />}
      {tab === "revenue" && (
        <Revenue token={token} companyId={companyId} name={h.name} ticker={ticker} saved={saved ?? null} onOpenChart={onOpenChart} />
      )}
      {tab === "segments" && <Segments token={token} companyId={companyId} name={h.name} />}
      {tab === "ratios" && <Ratios token={token} companyId={companyId} name={h.name} ticker={ticker} notApplicable={row.not_applicable} />}
      {tab === "filings" && <Filings token={token} companyId={companyId} name={h.name} canRead={row.access.includes("filings")} fiscalYearEnd={row.fiscal_year_end} />}
      {tab === "notes" && <DataNotes token={token} companyId={companyId} name={h.name} canReadFilings={row.access.includes("filings")} />}
      {tab === "peers" && <Peers token={token} companyId={companyId} name={h.name} onChart={onChartMany} />}
      {tab === "price" && <Price token={token} companyId={companyId} name={h.name} />}
    </div>
  );
}

/** The two ways to start research, side by side where the user chooses (L5): an analysis (the
 * guided project, its thesis and model inside it) or, beside it, a single thesis or model in a
 * folder (NewWork). Opens the company's analysis if there is one. */
function AnalysisLink({ saved, companyId, ticker }: { saved: Saved; companyId: string; ticker: string }) {
  const list = saved.list.state === "ok" ? saved.list.workspaces : [];
  const project = list.find((w) => restoreProject(w.project)?.companyId === companyId);
  return project ? (
    <a className="btn" href={href({ view: "analysis", projectId: project.workspace_id })}
      title={`Your guided analysis of ${ticker}: business, numbers, earnings, what changed, peers, model, thesis and monitor`}>
      Open analysis
    </a>
  ) : (
    <a className="btn" href={href({ view: "analysis", start: companyId })}
      title={`Eight guided steps for ${ticker} (business, numbers, earnings, what changed, peers, model, thesis, monitor), with its thesis and model inside. New thesis and New model make a single item instead.`}>
      Start an analysis
    </a>
  );
}

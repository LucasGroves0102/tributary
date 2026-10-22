import { Fragment, useState, type ReactNode } from "react";

import { ErrorPanel, Skeleton } from "../components/States";
import { navRow, onTableKey } from "../components/tableNav";
import { ago, change, day } from "../format";
import { status, type ExpectedItem, type FilingItem, type WatchRow } from "../home/data";
import { EXPECTED_DAYS, RECENT_DAYS, type Workspace } from "../home/useWorkspace";
import { go, href } from "../route";
import { defaultHome, type HomeCard, type HomeCardId } from "../views/model";

const FEED_ROWS = 10;

interface Props {
  workspace: Workspace;
  lastIngested: string | null;
  onAdd: () => void;
  onExport: () => void;
  /** "Continue where you left off" (workspaces/Recent) */
  recent?: ReactNode;
  /** "Recent data notes for your companies" (home/RecentNotes) */
  notes?: ReactNode;
  /** the user's arrangement (views): cards in order, some hidden; default: all in order */
  layout?: HomeCard[];
  /** "Customize": opens the editor (views/Editors HomeLayoutEditor), shown above the cards */
  onCustomize?: () => void;
  editor?: ReactNode;
  /** the watchlist's "Columns" (views/Editors ColumnPicker) */
  columnPicker?: ReactNode;
}

/** Cards shown side by side when next to each other. */
const HALF = new Set<HomeCardId>(["filings", "expected"]);

export function Home({ workspace, lastIngested, onAdd, onExport, recent, notes, layout, onCustomize, editor, columnPicker }: Props) {
  const { watchlist, rows, filings, expected, since, firstVisit } = workspace;
  const newLabel = firstVisit
    ? "new in the last 7 days in bold"
    : `new since your last visit (${day(since)}) in bold`;

  const card = (id: HomeCardId): ReactNode => {
    switch (id) {
      case "status":
        return watchlist.state === "ok" ? (
          <StatusStrip rows={rows} filings={filings ?? []} since={since} firstVisit={firstVisit} lastIngested={lastIngested} />
        ) : null;
      case "recent":
        return recent ? (
          <section className="card">
            <div className="card-h">
              <h2>Continue where you left off</h2>
              <span className="sub">saved work you opened and filings you read, newest first</span>
            </div>
            {recent}
          </section>
        ) : null;
      case "watchlist":
        return (
          <section className="card">
            <div className="card-h">
              <h2>Watchlist</h2>
              <span className="sub">
                USD · TTM to each company's latest reported quarter unless noted · ~ = expected
              </span>
              <span className="grow" />
              {columnPicker}
              <button className="btn sm" onClick={onAdd}>
                + Add
              </button>
              <button className="btn sm" onClick={onExport} disabled={watchlist.state !== "ok"}>
                Export CSV
              </button>
            </div>
            <WatchTable workspace={workspace} onAdd={onAdd} />
          </section>
        );
      case "filings":
        return (
          <section className="card">
            <div className="card-h">
              <h2>
                New filings for {workspace.readableCount
                  ? `the ${workspace.readableCount} companies you can read`
                  : "your watchlist"}
              </h2>
              <span className="sub">
                last {RECENT_DAYS} days · {newLabel}
              </span>
            </div>
            <FilingFeed filings={filings} />
          </section>
        );
      case "expected":
        return (
          <section className="card">
            <div className="card-h">
              <h2>Expected filings</h2>
              <span className="sub">next {EXPECTED_DAYS} days · estimated from each company's history</span>
            </div>
            <ExpectedTable expected={expected} />
            <div className="foot">
              Estimates, not filings: each company's median filing lag over its recent reports,
              capped at the SEC deadline. Watchlist companies in bold.
            </div>
          </section>
        );
      case "notes":
        return notes ?? null;
    }
  };

  // visible cards in order; two half cards next to each other share a row
  const shown = (layout ?? defaultHome()).filter((c) => !c.hidden).map((c) => c.id);
  const blocks: ReactNode[] = [];
  for (let i = 0; i < shown.length; i++) {
    const id = shown[i]!;
    const next = shown[i + 1];
    if (HALF.has(id) && next && HALF.has(next)) {
      blocks.push(
        <div className="grid2" key={`${id}-${next}`}>
          {card(id)}
          {card(next)}
        </div>,
      );
      i++;
    } else {
      const el = card(id);
      if (el) blocks.push(<Fragment key={id}>{el}</Fragment>);
    }
  }

  return (
    <div className="page">
      <div className="row">
        <h1 className="grow">Home</h1>
        {onCustomize && (
          <button className="btn ghost" onClick={onCustomize} aria-label="Customize Home">
            Customize
          </button>
        )}
        <button className="btn" onClick={() => go({ view: "companies" })}>
          Screen companies
        </button>
        <button className="btn" onClick={() => go({ view: "compare" })}>
          New comps
        </button>
        <button className="btn primary" onClick={() => go({ view: "chart" })}>
          New chart
        </button>
      </div>

      {editor}
      {blocks}
    </div>
  );
}

function StatusStrip({
  rows,
  filings,
  since,
  firstVisit,
  lastIngested,
}: {
  rows: WatchRow[];
  filings: FilingItem[];
  since: string;
  firstVisit: boolean;
  lastIngested: string | null;
}) {
  const s = status(rows, filings);
  return (
    <div className="status-strip" role="list" aria-label="Status">
      <div role="listitem">
        <div className="k">Watchlist</div>
        <div className="v">
          {s.companies} {s.companies === 1 ? "company" : "companies"}
        </div>
        <div className="p">
          {s.industries} {s.industries === 1 ? "industry" : "industries"}
        </div>
      </div>
      <div role="listitem">
        <div className="k">New since {firstVisit ? "a week ago" : day(since)}</div>
        <div className="v">
          {s.newFilings} {s.newFilings === 1 ? "filing" : "filings"}
        </div>
        <div className="p">
          {s.newPeriodic} 10-K/10-Q · {s.new8k} 8-K
        </div>
      </div>
      <div role="listitem" title={s.next?.expected.basis}>
        <div className="k">Next expected</div>
        {s.next ? (
          <>
            <div className="v">
              {s.next.row.ticker ?? s.next.row.name} {s.next.expected.form} ~
              {day(s.next.expected.expected_date!)}
            </div>
            <div className="p">
              estimate · period ending {day(s.next.expected.period_end)}
              {s.next.expected.typical_lag_days !== null &&
                ` · typical lag ${s.next.expected.typical_lag_days} days`}
            </div>
          </>
        ) : (
          <>
            <div className="v faint">—</div>
            <div className="p">no estimate for your watchlist yet</div>
          </>
        )}
      </div>
      <div role="listitem">
        <div className="k">Median revenue growth</div>
        {s.medianGrowth !== null ? (
          <>
            <div className={`v ${s.medianGrowth >= 0 ? "pos" : "neg"}`}>{change(s.medianGrowth)}</div>
            <div className="p">
              TTM YoY{s.best && ` · best ${s.best.row.ticker ?? s.best.row.name} ${change(s.best.growth)}`}
            </div>
          </>
        ) : (
          <>
            <div className="v faint">—</div>
            <div className="p">needs financials access to a watched company</div>
          </>
        )}
      </div>
      <div role="listitem">
        <div className="k">Data freshness</div>
        {lastIngested ? (
          <>
            <div className="v">
              <span className="live" />
              Live
            </div>
            <div className="p">last filing ingested {ago(lastIngested)}</div>
          </>
        ) : (
          <>
            <div className="v faint">—</div>
            <div className="p">shown for companies with filings access</div>
          </>
        )}
      </div>
    </div>
  );
}

function WatchTable({ workspace, onAdd }: { workspace: Workspace; onAdd: () => void }) {
  const { watchlist, rows } = workspace;
  const [removing, setRemoving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (watchlist.state === "loading") return <Skeleton lines={4} label="Loading your watchlist" />;
  if (watchlist.state === "unavailable") {
    return (
      <p className="empty">
        Watchlists are offline right now. Your companies are under{" "}
        <a href={href({ view: "companies" })}>Companies</a> in the meantime.
      </p>
    );
  }
  if (watchlist.state === "error") {
    return <ErrorPanel what="your watchlist" detail={`The Query API said: ${watchlist.message}.`} onRetry={workspace.reload} />;
  }
  if (rows.length === 0) {
    return (
      <p className="empty">
        Follow companies here to see their latest filing, key numbers and next expected report.{" "}
        <button className="btn sm" onClick={onAdd}>
          + Add a company
        </button>{" "}
        or press <span className="kbd">Ctrl K</span> and type <code>watch:</code>.
      </p>
    );
  }

  const remove = (id: string) => {
    setRemoving(id);
    setError(null);
    workspace
      .unwatch(id)
      .catch(() => setError("Couldn't remove it. Try again."))
      .finally(() => setRemoving(null));
  };

  return (
    <>
      {error && <p className="empty error">{error}</p>}
      <div className="tw">
        <table className="t watchlist">
          <thead>
            <tr>
              <th className="first">Company</th>
              <th>Last filing</th>
              <th>Period</th>
              {workspace.columns.map((c) => (
                <th key={c.key} className="n">
                  {c.label} <span className="unit">{c.period}</span>
                </th>
              ))}
              <th>Next filing</th>
              <th>
                <span className="visually-hidden">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody onKeyDown={onTableKey}>
            {rows.map((r, i) => (
              <tr key={r.companyId} className="clk" {...navRow(i, r.name)} onClick={() => go({ view: "company", companyId: r.companyId })}>
                <td className="first">
                  <a className="co" href={href({ view: "company", companyId: r.companyId })} onClick={(e) => e.stopPropagation()}>
                    <b>{r.name}</b>
                    {r.ticker && <span className="faint mono">{r.ticker}</span>}
                  </a>
                </td>
                <td>
                  {r.last ? (
                    <span title={`${r.last.form} filed ${day(r.last.filed, true)}, ${r.last.accessionNo}`}>
                      <FormTag form={r.last.form} /> <span className="muted">{day(r.last.filed)}</span>
                    </span>
                  ) : (
                    <span className="faint">none on file</span>
                  )}
                </td>
                <td className="muted">
                  {r.last?.periodEnd ? (
                    <>
                      {r.last.fiscalPeriod ?? "Quarter"}{" "}
                      <span className="faint">(to {day(r.last.periodEnd, true)})</span>
                    </>
                  ) : (
                    "—"
                  )}
                </td>
                {workspace.columns.map((c) => {
                  const cell = r.cells[c.key]!;
                  const tone =
                    c.signed && cell.value !== null
                      ? cell.value >= 0
                        ? "pos"
                        : "neg"
                      : cell.value === null
                        ? "faint"
                        : "";
                  return (
                    <td key={c.key} className={`n ${tone}`} title={cell.title}>
                      {cell.text}
                    </td>
                  );
                })}
                <td title={r.next?.basis}>
                  {r.next?.expected_date ? (
                    <>
                      <span className="tag e">{r.next.form}</span>{" "}
                      <span className="muted">~{day(r.next.expected_date)}</span>
                      {r.next.status === "overdue" && <span className="tag warn">overdue</span>}
                    </>
                  ) : (
                    <span className="faint">no estimate</span>
                  )}
                </td>
                <td className="n" onClick={(e) => e.stopPropagation()}>
                  <button
                    className="btn sm ghost"
                    disabled={removing === r.companyId}
                    onClick={() => remove(r.companyId)}
                    aria-label={`Remove ${r.name} from watchlist`}
                  >
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function FormTag({ form }: { form: string }) {
  const kind = form.startsWith("10-K") ? "k" : form.startsWith("10-Q") ? "q" : "";
  return <span className={`tag ${kind}`}>{form}</span>;
}

function FilingFeed({ filings }: { filings: FilingItem[] | null }) {
  if (filings === null) return <Skeleton lines={5} label="Loading recent filings" />;
  if (filings.length === 0) {
    return (
      <p className="empty">
        Nothing filed by your companies in the last {RECENT_DAYS} days. Add companies to your
        watchlist to follow their filings here.
      </p>
    );
  }
  return (
    <ul className="feed">
      {filings.slice(0, FEED_ROWS).map((f) => (
        <li key={f.accessionNo} className={f.isNew ? "it new" : "it"}>
          <span className="muted num">{day(f.filed)}</span>
          <FormTag form={f.form} />
          <span className="feed-name">
            {f.isNew ? <b>{f.name}</b> : f.name}
            {f.periodEnd && <span className="muted"> · period to {day(f.periodEnd, true)}</span>}
          </span>
          <span className="row">
            {f.readable ? (
              <a
                className="btn sm ghost"
                href={href({ view: "filing", companyId: f.companyId, accessionNo: f.accessionNo, section: null })}
              >
                Read
              </a>
            ) : (
              <a
                className="btn sm ghost"
                href={href({ view: "company", companyId: f.companyId, tab: "filings" })}
                title="Reading filing text needs the pro plan and a grant for this company"
              >
                Open
              </a>
            )}
          </span>
        </li>
      ))}
    </ul>
  );
}

function ExpectedTable({ expected }: { expected: ExpectedItem[] | null }) {
  if (expected === null) return <Skeleton lines={4} label="Loading expected filings" />;
  if (expected.length === 0) {
    return (
      <p className="empty">
        No 10-K or 10-Q expected from your companies in the next {EXPECTED_DAYS} days. Watch more
        companies to see their reporting dates here.
      </p>
    );
  }
  return (
    <div className="tw">
      <table className="t">
        <thead>
          <tr>
            <th>Date</th>
            <th>Company</th>
            <th>Form</th>
            <th>Period end</th>
          </tr>
        </thead>
        <tbody>
          {expected.map((e) => (
            <tr
              key={`${e.company_id}-${e.form}-${e.period_end}`}
              className="clk"
              title={e.basis}
              onClick={() => go({ view: "company", companyId: e.company_id })}
            >
              <td className="muted">
                ~{e.expected_date ? day(e.expected_date) : "—"}
                {e.status === "overdue" && <span className="tag warn">overdue</span>}
              </td>
              <td>{e.watched ? <b>{e.name ?? e.company_id}</b> : (e.name ?? e.company_id)}</td>
              <td>
                <span className="tag e">{e.form}</span>
              </td>
              <td className="muted">{day(e.period_end, true)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

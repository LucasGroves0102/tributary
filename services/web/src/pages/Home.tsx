import { Fragment, useEffect, useState, type ReactNode } from "react";

import { Choices } from "../components/Choices";
import { FigureText } from "../components/FigureText";
import { FormMark } from "../components/FormMark";
import { toast } from "../components/Toasts";
import { ErrorPanel, LedgerShape } from "../components/States";
import { navRow, onTableKey } from "../components/tableNav";
import { ago, change, day, lateFiling, periodPhrase, UNNAMED } from "../format";
import { sinceLabel, status, type ExpectedItem, type FilingItem, type WatchRow } from "../home/data";
import { FRESHNESS_EXPLAINED } from "../home/useFeedStatus";
import { EXPECTED_DAYS, RECENT_DAYS, type Workspace } from "../home/useWorkspace";
import { edgarOpen } from "../components/Sidebar";
import { go, href } from "../route";
import { load, save } from "../storage";
import { defaultHome, type HomeCard, type HomeCardId } from "../views/model";
import { TableScroll } from "../components/TableScroll";

const FEED_ROWS = 10;

interface Props {
  workspace: Workspace;
  /** the newest filing's text parsed, among those the user can read */
  lastIngested: string | null;
  /** the newest filing in the index, whoever's (useFeedStatus) */
  listed?: string | null;
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
  /** how many companies the user can read (access.ts readableTotal): the feed covers them all */
  readableCompanies?: number;
  /** "New chart": an empty chart, the current draft kept for one Restore */
  onNewChart?: () => void;
  /** the first step (home/StartCard: start or continue an analysis), above the cards */
  start?: ReactNode;
}

/** Cards shown side by side when next to each other. */
/** Cards that sit beside the new filings when they follow them (spec §6 Home: the side column). */
const SIDE = new Set<HomeCardId>(["expected", "notes"]);

export function Home({ workspace, lastIngested, listed = null, onAdd, onExport, recent, notes, layout, onCustomize, editor, columnPicker, readableCompanies = 0, onNewChart, start }: Props) {
  const { watchlist, rows, filings: allFilings, expected, since, firstVisit } = workspace;
  // the feed starts with the watchlist's filings (Riley 49: with the catalog open it was everyone's latest,
  // microcaps and ETFs); All companies is one click
  const watchedIds = new Set(rows.map((r) => r.companyId));
  const [feedScope, setFeedScope] = useState<"watchlist" | "all">("watchlist");
  const scoped = feedScope === "watchlist" && watchedIds.size > 0 && readableCompanies > 0;
  const filings = allFilings && scoped ? allFilings.filter((f) => watchedIds.has(f.companyId)) : allFilings;
  // bold tells the new rows from the rest, so it says nothing when they're all new or none are
  const shownFilings = filings?.slice(0, FEED_ROWS) ?? [];
  const someNew = shownFilings.some((f) => f.isNew);
  const allNew = shownFilings.length > 0 && shownFilings.every((f) => f.isNew);
  const newLabel = allNew ? `All filed ${sinceLabel(since, firstVisit)}` : someNew ? `In bold: filed ${sinceLabel(since, firstVisit)}` : `None filed ${sinceLabel(since, firstVisit)}`;

  const card = (id: HomeCardId): ReactNode => {
    switch (id) {
      case "status":
        return watchlist.state === "ok" ? (
          <StatusStrip rows={rows} fresh={workspace.filings === null ? null : workspace.fresh} scope={workspace.freshScope} since={sinceLabel(since, firstVisit)} lastIngested={lastIngested} listed={listed} />
        ) : watchlist.state === "loading" ? <StatusShape /> : null;
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
                US dollars, the twelve months to each company's latest quarter unless a figure says otherwise; ~ is an estimate
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
              <h2>New filings</h2>
              <span className="sub">
                Up to {RECENT_DAYS} days back. {newLabel}.
              </span>
              {readableCompanies > 0 && watchedIds.size > 0 && (
                <>
                  <span className="grow" />
                  <Choices label="Whose filings" value={feedScope} set={setFeedScope}
                    options={[["watchlist", "Your watchlist"], ["all", `All ${readableCompanies.toLocaleString("en-US")} in your plan`]]} />
                </>
              )}
            </div>
            <FilingFeed filings={filings} bold={someNew && !allNew} />
          </section>
        );
      case "expected":
        return (
          <section className="card">
            <div className="card-h">
              <h2>Expected filings</h2>
              <span className="sub">next {EXPECTED_DAYS} days · estimated from each company's history</span>
            </div>
            <ExpectedTable expected={expected} />
            <div className="foot">
              Estimates, not filings (a dashed tag means not filed yet): each company's median filing lag over its
              recent reports, capped at the SEC deadline. Watchlist companies in bold.
            </div>
          </section>
        );
      case "notes":
        return notes ?? null;
    }
  };

  // visible cards in order (Customize); the new filings lead, and the side cards right after them (expected
  // filings, data notes) sit in a side column beside them (spec §6: lead 7, side 5; stacked when narrow)
  const shown = (layout ?? defaultHome()).filter((c) => !c.hidden).map((c) => c.id);
  const blocks: ReactNode[] = [];
  for (let i = 0; i < shown.length; i++) {
    const id = shown[i]!;
    if (id === "filings") {
      const side: HomeCardId[] = [];
      while (i + 1 < shown.length && SIDE.has(shown[i + 1]!)) side.push(shown[++i]!);
      const aside = side.map((s) => card(s)).filter(Boolean);
      blocks.push(aside.length ? (
        <div className="home-split" key="filings">
          <div className="home-lead">{card(id)}</div>
          <div className="home-side">{side.map((s) => <Fragment key={s}>{card(s)}</Fragment>)}</div>
        </div>
      ) : <Fragment key={id}>{card(id)}</Fragment>);
    } else {
      const el = card(id);
      if (el) blocks.push(<Fragment key={id}>{el}</Fragment>);
    }
  }

  return (
    <div className="page">
      <div className="row page-bar">
        <h1 className="grow">Home</h1>
        {onCustomize && (
          <button className="btn ghost" onClick={onCustomize} aria-label="Customize Home">
            Customize
          </button>
        )}
        {/* #127: one way in leads (the start card); these are the others, quietly */}
        <button className="btn ghost" onClick={() => go({ view: "companies" })}>
          Screen companies
        </button>
        <button className="btn ghost" onClick={() => go({ view: "compare" })}>
          New comps
        </button>
        <button className="btn ghost" onClick={() => (onNewChart ? onNewChart() : go({ view: "chart" }))}>
          New chart
        </button>
      </div>

      {editor}
      {start}
      {blocks}
    </div>
  );
}

function StatusStrip({
  rows,
  fresh,
  scope,
  since,
  lastIngested,
  listed,
}: {
  rows: WatchRow[];
  /** the new filings to count (Workspace.fresh); null while they load, which isn't "0 filings" (L6) */
  fresh: FilingItem[] | null;
  scope: string;
  /** sinceLabel */
  since: string;
  lastIngested: string | null;
  listed: string | null;
}) {
  const s = status(rows, fresh ?? []);
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
        <div className="k">New {since}</div>
        {fresh === null ? (
          <>
            <div className="v faint" aria-busy="true">…</div>
            <div className="p">On {scope}; counting</div>
          </>
        ) : (
          <>
            <div className="v">
              {s.newFilings} {s.newFilings === 1 ? "filing" : "filings"}
            </div>
            <div className="p">
              On {scope}: {s.newPeriodic} 10-K or 10-Q, {s.new8k} 8-K
            </div>
          </>
        )}
      </div>
      <div role="listitem" title={s.next?.expected.basis}>
        <div className="k">Next expected</div>
        {s.next ? (
          <>
            {/* the date is the figure; whose and which form under it, so it never wraps (Riley 128) */}
            <div className="v">~{day(s.next.expected.expected_date!)}{lateFiling(s.next.expected) && <span className="tag warn">late</span>}</div>
            <div className="p">
              {s.next.row.ticker ?? s.next.row.name} {s.next.expected.form}, an estimate for the period ending {day(s.next.expected.period_end)}
              {s.next.expected.typical_lag_days !== null &&
                `; it usually files ${s.next.expected.typical_lag_days} days after`}
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
              Twelve months on a year earlier{s.best && `; best ${s.best.row.ticker ?? s.best.row.name} ${change(s.best.growth)}`}
            </div>
          </>
        ) : (
          <>
            <div className="v faint">—</div>
            <div className="p">needs financials access to a watched company</div>
          </>
        )}
      </div>
      <div role="listitem" title={FRESHNESS_EXPLAINED}>
        <div className="k">Newest filing listed</div>
        {listed ? (
          <div className="v">
            <span className={edgarOpen() ? "live" : "live quiet"} />
            {ago(listed)}
          </div>
        ) : (
          <div className="v faint">—</div>
        )}
        <div className="p">{lastIngested ? `newest filing text parsed ${ago(lastIngested)}` : "filing text appears for companies with filings access"}</div>
      </div>
    </div>
  );
}

/** How many rows the watchlist had (a per-browser convenience: its loading shape's height). */
const SIZE_KEY = "tributary.watchlist.size";

/** The status strip's shape while the watchlist loads: its labels, the values blank, so the page under it
 * doesn't drop when it comes (Marek 12: Home's lead and side moved 166px, CLS 0.171). */
function StatusShape() {
  const labels = ["Watchlist", "New filings", "Next expected", "Median revenue growth", "Newest filing listed"];
  return (
    <div className="status-strip shape" aria-busy="true">
      {labels.map((k) => (
        <div key={k}>
          <div className="k">{k}</div>
          <div className="v"><span className="blank-v w-64" /></div>
          <div className="p"><span className="blank-v w-160" /></div>
        </div>
      ))}
    </div>
  );
}

function WatchTable({ workspace, onAdd }: { workspace: Workspace; onAdd: () => void }) {
  const { watchlist, rows } = workspace;
  const [removing, setRemoving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // headers sort (Riley 70); the order is this page's, not saved
  const [sort, setSort] = useState<{ key: string; desc: boolean } | null>(null);
  const sortBy = (key: string, numeric: boolean) =>
    setSort((s) => (s?.key === key ? { key, desc: !s.desc } : { key, desc: numeric }));
  const sortedRows = sort ? sortWatch(rows, sort.key, sort.desc) : rows;
  const head = (key: string, label: ReactNode, numeric: boolean, unit?: string) => (
    <button type="button" className="th-sort" onClick={() => sortBy(key, numeric)}>
      {label}{sort?.key === key ? (sort.desc ? " ↓" : " ↑") : ""}
      {unit && <> <span className="unit">{unit}</span></>}
    </button>
  );
  // the shape while it loads has as many rows as the list had last time, so the page under it stays put
  // when it comes (Marek 12: the watchlist grew 166px and pushed the filings down)
  const [shapeRows] = useState(() => Math.min(20, Math.max(1, Number(load(SIZE_KEY)) || 6)));
  useEffect(() => {
    if (watchlist.state === "ok") save(SIZE_KEY, String(Math.max(1, rows.length)));
  }, [watchlist.state, rows.length]);
  const ariaSort = (key: string) => (sort?.key === key ? (sort.desc ? "descending" as const : "ascending" as const) : undefined);
  // a removal is a toast with Undo (components/Toasts), like every other confirmation
  const removedToast = (id: string, name: string) => toast({
    text: `Removed ${name} from your watchlist.`, topic: `unwatch-${id}`,
    action: { label: "Undo", onClick: () => void workspace.watch(id).catch(() => setError("Couldn't put it back. Add it again with + Add.")) },
  });

  if (watchlist.state === "loading") {
    return <LedgerShape label="Loading your watchlist" first="Company" rows={shapeRows} lines={2}
      columns={["Last filing", ...workspace.columns.map((c) => c.label), "Next filing"]} />;
  }
  if (watchlist.state === "unavailable") {
    return (
      <p className="empty">
        Watchlists are offline right now. Your companies are under{" "}
        <a href={href({ view: "companies" })}>Companies</a> in the meantime.
      </p>
    );
  }
  if (watchlist.state === "error") {
    return <ErrorPanel what="your watchlist" detail={watchlist.message} onRetry={workspace.reload} />;
  }
  if (rows.length === 0) {
    return (
      <>
      <p className="empty">
        Follow companies here to see their latest filing, key numbers and next expected report.{" "}
        <button className="btn sm" onClick={onAdd}>
          + Add a company
        </button>{" "}
        or press <span className="kbd">Ctrl K</span> and type <code>watch:</code>.
      </p>
      </>
    );
  }

  const remove = (id: string, name: string) => {
    setRemoving(id);
    setError(null);
    workspace
      .unwatch(id)
      .then(() => removedToast(id, name))
      .catch(() => setError("Couldn't remove it. Try again."))
      .finally(() => setRemoving(null));
  };

  return (
    <>
      {error && <p className="empty error">{error}</p>}
      <TableScroll label="Watchlist">
        <table className="t watchlist">
          <thead>
            <tr>
              <th className="first" aria-sort={ariaSort("name")}>{head("name", "Company", false)}</th>
              <th aria-sort={ariaSort("last")}>{head("last", "Last filing", true)}</th>
              {workspace.columns.map((c) => (
                <th key={c.key} className={c.label.length > 14 ? "n long" : "n"} aria-sort={ariaSort(c.key)}>
                  {head(c.key, c.label, true, c.period)}
                </th>
              ))}
              <th aria-sort={ariaSort("next")}>{head("next", "Next filing", false)}</th>
              <th>
                <span className="visually-hidden">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody onKeyDown={onTableKey}>
            {sortedRows.map((r, i) => (
              <tr key={r.companyId} className="clk" {...navRow(i, r.name)} onClick={() => go({ view: "company", companyId: r.companyId })}>
                <td className="first">
                  <a className="co" href={href({ view: "company", companyId: r.companyId })} onClick={(e) => e.stopPropagation()}>
                    <b data-entity-name="">{r.name}</b>
                    {r.ticker && <span className="faint mono">{r.ticker}</span>}
                  </a>
                </td>
                {/* the filing and its period in one column, the period a line under it (Riley 106: a 200px
                    period column); the accession number is the hover */}
                <td className="last-filing" data-label="Last filing"
                  title={r.last ? `${r.last.form} filed ${day(r.last.filed, true)}${r.last.periodEnd ? `, for the period to ${day(r.last.periodEnd, true)}` : ""}. ${r.last.accessionNo}` : undefined}>
                  {r.last ? (
                    <>
                      <FormMark form={r.last.form} title={null} /> <span className="muted">{day(r.last.filed)}</span>
                      {r.last.periodEnd && <span className="faint small period-sub">{r.last.fiscalPeriod ?? `to ${day(r.last.periodEnd)}`}</span>}
                    </>
                  ) : (
                    <span className="faint">none on file</span>
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
                    <td key={c.key} className={`n ${tone}`} title={cell.title} data-label={`${c.label}, ${c.period}`}>
                      <FigureText cell={cell} />
                    </td>
                  );
                })}
                <td title={r.next?.basis} data-label="Next filing">
                  {r.next?.expected_date ? (
                    <>
                      <span className="tag e">{r.next.form}</span>{" "}
                      <span className="muted">~{day(r.next.expected_date)}</span>
                      {r.next.status === "overdue" && <span className="tag warn">overdue</span>}
                      {/* its estimated date has passed: late, not "about" a past day (Marek 9) */}
                      {lateFiling(r.next) && <span className="tag warn">late</span>}
                    </>
                  ) : (
                    <span className="faint">no estimate</span>
                  )}
                </td>
                <td className="n actions" onClick={(e) => e.stopPropagation()}>
                  <button
                    className="btn sm ghost"
                    disabled={removing === r.companyId}
                    onClick={() => remove(r.companyId, r.name)}
                    aria-label={`Remove ${r.name} from watchlist`}
                  >
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
    </>
  );
}


/** The watchlist in a header's order: by name, last filing date, a value (its comparable figure: blanks
 * last, whatever the direction) or the next expected date. */
function sortWatch(rows: WatchRow[], key: string, desc: boolean): WatchRow[] {
  const value = (r: WatchRow): string | number | null => {
    if (key === "name") return r.name.toLowerCase();
    if (key === "last") return r.last?.filed ?? null;
    if (key === "next") return r.next?.expected_date ?? null;
    return r.cells[key]?.value ?? null;
  };
  return [...rows].sort((a, b) => {
    const x = value(a);
    const y = value(b);
    if (x === null || y === null) return x === y ? 0 : x === null ? 1 : -1;
    const c = x < y ? -1 : x > y ? 1 : 0;
    return desc ? -c : c;
  });
}

function FilingFeed({ filings, bold }: { filings: FilingItem[] | null; bold: boolean }) {
  if (filings === null) return <LedgerShape label="Loading recent filings" first="Filed" columns={["Form", "Company"]} rows={5} />;
  if (filings.length === 0) {
    return (
      <p className="empty">
        Nothing filed by your companies in the last {RECENT_DAYS} days. Add companies to your
        watchlist to follow their filings here.
      </p>
    );
  }
  // grouped by the day filed (spec §6 Home): the date once, at the head of its day
  const shown = filings.slice(0, FEED_ROWS);
  return (
    <ul className="feed">
      {shown.map((f, i) => {
        const first = i === 0 || shown[i - 1]!.filed.slice(0, 10) !== f.filed.slice(0, 10);
        return (
          <li key={f.accessionNo} className={[f.isNew ? "it new" : "it", first && "day-first"].filter(Boolean).join(" ")}>
            <span className="feed-date num">{first ? day(f.filed) : <span className="visually-hidden">{day(f.filed)}</span>}</span>
            <FormMark form={f.form} />
            <span className="feed-name">
              {f.isNew && bold ? <b>{f.name}</b> : f.name}
              {f.periodEnd && <span className="muted feed-period">, {periodPhrase(f.form, f.periodEnd)}</span>}
            </span>
            <span className="feed-act">
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
              {/* a 10-K or 10-Q opens on what changed in it (spec §6 Home) */}
              {f.readable && /^10-[KQ]$/.test(f.form) && (
                <a className="btn sm ghost" href={href({ view: "filing", companyId: f.companyId, accessionNo: f.accessionNo, section: null, mode: "changes" })}>
                  What changed
                </a>
              )}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function ExpectedTable({ expected }: { expected: ExpectedItem[] | null }) {
  if (expected === null) return <LedgerShape label="Loading expected filings" first="Date" columns={["Company", "Form", "Period end"]} rows={4} />;
  if (expected.length === 0) {
    return (
      <p className="empty">
        No 10-K or 10-Q expected from your companies in the next {EXPECTED_DAYS} days. Watch more
        companies to see their reporting dates here.
      </p>
    );
  }
  return (
    <TableScroll label="Expected filings">
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
                {lateFiling(e) && <span className="tag warn">late</span>}
              </td>
              <td>{e.watched ? <b>{e.name ?? UNNAMED}</b> : (e.name ?? UNNAMED)}</td>
              <td>
                <span className="tag e">{e.form}</span>
              </td>
              <td className="muted">{day(e.period_end, true)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}

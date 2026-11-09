import { useEffect, useRef, type ReactNode } from "react";

import { accountName, readableTotal } from "../access";
import { ago, day } from "../format";
import { sinceLabel, type WatchRow } from "../home/data";
import { FRESHNESS_EXPLAINED } from "../home/useFeedStatus";
import type { Workspace } from "../home/useWorkspace";
import { href, NAV, type Route } from "../route";
import type { Me } from "../types";
import { Spark } from "./Spark";
import { Skeleton } from "./States";

const NEXT_FILINGS = 4;

interface Props {
  route: Route;
  me: Me | null;
  workspace: Workspace;
  /** the newest filing's text parsed, among those the user can read */
  lastIngested: string | null;
  /** the newest filing in the index, whoever's (useFeedStatus) */
  listed?: string | null;
  onAdd: () => void;
  onExport: () => void;
  /** opens the keyboard shortcut sheet */
  onShortcuts?: () => void;
  /** the Workspaces section (workspaces/WorkspaceTree) */
  workspaces?: ReactNode;
  /** narrow screens: shown as a drawer over the page */
  open?: boolean;
  /** the server offers "Connect your data": show Settings → Connections */
  connections?: boolean;
  /** this is a temporary demo sandbox: how long until it resets ("3h 20m") */
  sandboxResetsIn?: string | null;
  /** a phone's top bar has no room for it */
  onSignOut?: () => void;
}

/** Which nav entry a route belongs to: a company or filing is under Companies. */
function section(route: Route): Route["view"] {
  return route.view === "company" || route.view === "filing" ? "companies" : route.view;
}

export function Sidebar({ route, me, workspace, lastIngested, listed = null, onAdd, onExport, onShortcuts, workspaces, open = false, connections = false, sandboxResetsIn = null, onSignOut }: Props) {
  // the phone menu always opens at the top of its lists
  const lists = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (open && lists.current) lists.current.scrollTop = 0;
  }, [open]);
  const { rows, watchlist } = workspace;
  const active = section(route);
  const newCount = workspace.fresh.length;
  const newTitle = `${newCount} new ${newCount === 1 ? "filing" : "filings"} on ${workspace.freshScope} ${sinceLabel(workspace.since, workspace.firstVisit)}`;
  const next = rows
    .filter((r) => r.next?.expected_date)
    .sort((a, b) => a.next!.expected_date!.localeCompare(b.next!.expected_date!))
    .slice(0, NEXT_FILINGS);

  return (
    <aside id="side-nav" className={open ? "side open" : "side"} aria-label="Navigation">
      <div className="side-top">
      <div className="brand">
        <img src="/favicon.svg" alt="" width={18} height={18} />
        Tributary
        {sandboxResetsIn && (
          <span className="env" title={`A temporary demo sandbox: it and what you save in it are deleted in ${sandboxResetsIn}`}>
            demo
          </span>
        )}
      </div>

      <nav className="nav" aria-label="Screens">
        {NAV.map((n) => (
          <a
            key={n.view}
            href={href({ view: n.view })}
            className={active === n.view ? "on" : undefined}
            aria-current={active === n.view ? "page" : undefined}
          >
            <span className="grow">{n.label}</span>
            {n.view === "home" && newCount > 0 && (
              <span className="cnt new" title={newTitle}>
                {newCount}
              </span>
            )}
            {n.view === "companies" && me && (
              <span className="cnt" title="Companies you can read">
                {readableTotal(me).toLocaleString("en-US")}
              </span>
            )}
            <kbd>g{n.key}</kbd>
          </a>
        ))}
      </nav>
      </div>

      <div className="side-lists" ref={lists}>
      <div className="sect">
        <span className="grow">Watchlist{watchlist.state === "ok" ? ` · ${rows.length}` : ""}</span>
        <button onClick={onExport} title="Export CSV" aria-label="Export watchlist CSV">
          <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
            <path d="M6 1v7M3 5.5 6 8.5l3-3M1.5 11h9" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
        <button onClick={onAdd} title="Add company" aria-label="Add company to watchlist">
          +
        </button>
      </div>
      <div className="wl">
        {watchlist.state === "loading" && <Skeleton lines={3} label="Loading watchlist" />}
        {watchlist.state === "unavailable" && (
          <p className="side-note">Watchlists are offline; your companies are under Companies.</p>
        )}
        {watchlist.state === "error" && <p className="side-note">Couldn't load the watchlist.</p>}
        {watchlist.state === "ok" && rows.length === 0 && (
          <p className="side-note">Press + to follow a company's filings here.</p>
        )}
        {rows.map((r) => (
          <WatchLink key={r.companyId} row={r} current={route} since={sinceLabel(workspace.since, workspace.firstVisit)} />
        ))}
      </div>

      {next.length > 0 && (
        <>
          <div className="sect">
            <span className="grow">Next filings (est.)</span>
          </div>
          <div className="up">
            {next.map((r) => (
              <a
                key={r.companyId}
                href={href({ view: "company", companyId: r.companyId })}
                title={`${r.name}: ${r.next!.form} for the period ending ${day(r.next!.period_end, true)}. ${r.next!.basis}`}
              >
                <b>{r.ticker ?? r.name}</b>
                <span className="tag e">{r.next!.form}</span>
                <span className="grow" />
                <span className="num">~{day(r.next!.expected_date!)}</span>
              </a>
            ))}
          </div>
        </>
      )}

      {workspaces}
      </div>

      <div className="side-foot">
      <details className="fresh" title={FRESHNESS_EXPLAINED}>
        <summary>
          {listed ? (
            <>
              <span className={edgarOpen() ? "live" : "live quiet"} />
              Newest filing listed {ago(listed)}
            </>
          ) : (
            "Data freshness"
          )}
        </summary>
        {listed && !edgarOpen() && (
          <>
            EDGAR's feed slows after 5:30 pm ET and on weekends
            <br />
          </>
        )}
        {lastIngested ? `Newest filing text parsed ${ago(lastIngested)}` : !listed && "Filings: nothing you can read yet"}
        <br />
        {watchlist.state === "ok"
          ? `Watchlist values as of ${new Date(watchlist.data.as_of).toLocaleTimeString([], {
              hour: "2-digit",
              minute: "2-digit",
              timeZoneName: "short",
            })}`
          : "Values appear as they're filed"}
        {me && (
          <>
            <br />
            Signed in as {accountName(me)} · {readableTotal(me).toLocaleString("en-US")} readable{" "}
            {readableTotal(me) === 1 ? "company" : "companies"}
          </>
        )}
      </details>
      {onSignOut && (
        <button className="btn sm side-signout" onClick={onSignOut}>
          Sign out
        </button>
      )}
      <div className="side-links">
      <a className="cover-link" href={href({ view: "about" })} aria-current={route.view === "about" ? "page" : undefined}
        title="No prices, consensus or transcripts">
        What Tributary covers
      </a>
      <a className="cover-link" href={href({ view: "status" })} aria-current={route.view === "status" ? "page" : undefined}
        title="Freshness, completeness, checks">
        Data status
      </a>
      {connections && (
        <a className="cover-link" href={href({ view: "settings" })} aria-current={route.view === "settings" ? "page" : undefined}
          title="Connect your market data">
          Settings
        </a>
      )}
      {onShortcuts && (
        <button className="link cover-link" onClick={onShortcuts}>
          Keyboard shortcuts <span className="kbd">?</span>
        </button>
      )}
      </div>
          <div className="credit">
        Built by{" "}
        <a href="https://github.com/LucasGroves0102" target="_blank" rel="noreferrer">Lucas Groves</a>
        {" · "}
        <a href="https://github.com/LucasGroves0102/tributary#architecture" target="_blank" rel="noreferrer">
          How it works
        </a>
        {" · "}
        <a href="https://github.com/LucasGroves0102/tributary" target="_blank" rel="noreferrer">GitHub</a>
      </div>
      </div>
    </aside>
  );
}

function WatchLink({ row, current, since }: { row: WatchRow; current: Route; since: string }) {
  const yoy = row.cells.revenue_growth_ttm!;
  const on = current.view === "company" && current.companyId === row.companyId;
  return (
    <a
      href={href({ view: "company", companyId: row.companyId })}
      className={on ? "on" : undefined}
      title={`${row.name} · revenue by quarter, last ${row.revenue.length || 12} quarters`}
    >
      <i className={row.isNew ? "d" : "d off"} title={row.isNew ? `New filing ${since}` : undefined} />
      <b>{row.ticker ?? row.name}</b>
      <Spark values={row.revenue} label={`${row.name} quarterly revenue`} />
      <span className={`num small ${yoy.value === null ? "faint" : yoy.value >= 0 ? "pos" : "neg"}`} title={yoy.title}>
        {yoy.value === null ? "" : yoy.text}
      </span>
    </a>
  );
}

/** EDGAR business hours (6:00 am-5:30 pm ET, weekdays), when filings reach its feed within
 * minutes; after them the feed goes quiet until the next morning. */
export function edgarOpen(now = new Date()): boolean {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", weekday: "short", hour: "numeric", minute: "numeric", hourCycle: "h23",
  }).formatToParts(now);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  const minutes = Number(get("hour")) * 60 + Number(get("minute"));
  return !["Sat", "Sun"].includes(get("weekday")) && minutes >= 6 * 60 && minutes < 17 * 60 + 30;
}

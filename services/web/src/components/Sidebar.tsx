import { useEffect, useRef, type ReactNode } from "react";

import { readableTotal } from "../access";
import { sinceLabel, type WatchRow } from "../home/data";
import type { Workspace } from "../home/useWorkspace";
import { href, NAV, type NavView, type Route } from "../route";
import type { Me } from "../types";
import { Skeleton } from "./States";

export { edgarOpen } from "./TopBar";

/** The rail's screens in the spec's order (§4), by their short names; the rest (Ask) stay a g-key and a
 * palette entry away. */
const RAIL: { view: NavView; label: string }[] = [
  { view: "home", label: "Home" },
  { view: "companies", label: "Companies" },
  { view: "compare", label: "Compare" },
  { view: "chart", label: "Charts" },
  { view: "analysis", label: "Analysis" },
  { view: "portfolio", label: "Portfolio" },
  { view: "data", label: "Your data" },
];

interface Props {
  route: Route;
  me: Me | null;
  workspace: Workspace;
  onAdd: () => void;
  onExport: () => void;
  /** opens the keyboard shortcut sheet */
  onShortcuts?: () => void;
  /** the Workspaces section (workspaces/WorkspaceTree) */
  workspaces?: ReactNode;
  /** narrow screens: shown as a drawer over the page */
  open?: boolean;
  /** the server offers "Connect your data": Settings' link says so */
  connections?: boolean;
  /** the way out on a narrow screen, where the top bar has no account chip */
  onSignOut?: () => void;
  /** "End demo" for a sandbox */
  signOutLabel?: string;
}

/** Which rail entry a route belongs to: a company or filing is under Companies. */
function section(route: Route): Route["view"] {
  return route.view === "company" || route.view === "filing" ? "companies" : route.view;
}

/** The rail (.interface-design/system.md §4), 232px on the paper with a hairline at its right, still on
 * every page change: the screens, then Watching (code, name and a "new" label for a new filing), then
 * Workspaces; its foot holds Data status, the API, Settings and the shortcuts. Under 1200px a drawer
 * over a scrim, from the top bar's Menu. Three parts: the screens stay put, the lists scroll, the foot
 * is always whole. */
export function Sidebar({ route, me, workspace, onAdd, onExport, onShortcuts, workspaces, open = false, connections = false, onSignOut, signOutLabel = "Sign out" }: Props) {
  // the phone menu always opens at the top of its lists
  const lists = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (open && lists.current) lists.current.scrollTop = 0;
  }, [open]);
  const { rows, watchlist } = workspace;
  const active = section(route);
  const newCount = workspace.fresh.length;
  const newTitle = `${newCount} new ${newCount === 1 ? "filing" : "filings"} on ${workspace.freshScope} ${sinceLabel(workspace.since, workspace.firstVisit)}`;
  const keys = new Map(NAV.map((n) => [n.view, n.key]));

  return (
    <aside id="side-nav" className={open ? "side open" : "side"} aria-label="Navigation">
      <div className="side-top">
        <nav className="nav" aria-label="Screens">
          {RAIL.map((n) => (
            <a
              key={n.view}
              href={href({ view: n.view })}
              className={active === n.view ? "on" : undefined}
              aria-current={active === n.view ? "page" : undefined}
              aria-keyshortcuts={keys.get(n.view) ? `g ${keys.get(n.view)}` : undefined}
            >
              <span className="grow">{n.label}</span>
              {n.view === "home" && newCount > 0 && (
                <span className="cnt new" title={newTitle}>
                  {newCount}
                </span>
              )}
              {n.view === "companies" && me && (
                <span className="cnt" title={COMPANIES_COUNTED}>
                  {readableTotal(me).toLocaleString("en-US")}
                </span>
              )}
            </a>
          ))}
        </nav>
      </div>

      <div className="side-lists" ref={lists}>
        <div className="sect">
          <span className="grow">Watching{watchlist.state === "ok" && <span className="sect-n">{rows.length}</span>}</span>
          <button className="icon-btn" onClick={onExport} title="Export CSV" aria-label="Export watchlist CSV">
            <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
              <path d="M6 1v7M3 5.5 6 8.5l3-3M1.5 11h9" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
          <button className="icon-btn" onClick={onAdd} title="Add company" aria-label="Add company to watchlist">
            <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
              <path d="M6 1.5v9M1.5 6h9" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
            </svg>
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

        {workspaces}
      </div>

      <div className="side-foot">
        {onSignOut && (
          <button className="btn side-signout" onClick={onSignOut}>
            {signOutLabel}
          </button>
        )}
        <div className="side-links">
          <a className="cover-link" href={href({ view: "status" })} aria-current={route.view === "status" ? "page" : undefined}
            title="Freshness, completeness, checks">
            Data status
          </a>
          {/* the API, its spreadsheet add-ins and the AI connection, from inside the app (Riley, test 10, finding 38) */}
          <a className="cover-link" href={href({ view: "api" })} aria-current={route.view === "api" ? "page" : undefined}
            title="Using the API: keys, spreadsheets, and your own AI model">
            API
          </a>
          {/* always: Settings holds filing alerts (F6), the theme and "Connect your data" (#60) */}
          <a className="cover-link" href={href({ view: "settings" })} aria-current={route.view === "settings" ? "page" : undefined}
            title={connections ? "Filing alerts, the theme and your market data" : "Filing alerts and the theme"}>
            Settings
          </a>
          {onShortcuts && (
            <button className="link cover-link" onClick={onShortcuts}>
              Shortcuts <kbd>?</kbd>
            </button>
          )}
        </div>
        <p className="credit">
          <a className="cover-link" href={href({ view: "about" })} aria-current={route.view === "about" ? "page" : undefined}
            title="No prices, consensus or transcripts">
            What Tributary covers
          </a>
          <span>
            Built by <a href="https://github.com/LucasGroves0102" target="_blank" rel="noreferrer">Lucas Groves</a>
            {" · "}
            <a href="https://github.com/LucasGroves0102/tributary#architecture" target="_blank" rel="noreferrer">How it works</a>
            {" · "}
            <a href="https://github.com/LucasGroves0102/tributary" target="_blank" rel="noreferrer">GitHub</a>
          </span>
        </p>
      </div>
    </aside>
  );
}

/** A watched company: its code, its name, and "new" when it filed since the last visit. No figures. */
function WatchLink({ row, current, since }: { row: WatchRow; current: Route; since: string }) {
  const on = current.view === "company" && current.companyId === row.companyId;
  return (
    <a
      href={href({ view: "company", companyId: row.companyId })}
      className={on ? "on" : undefined}
      aria-current={on ? "page" : undefined}
      title={row.isNew ? `${row.name}: a new filing ${since}` : row.name}
    >
      <b className="code">{row.ticker ?? ""}</b>
      <span className="wl-name" data-entity-name="">{row.name}</span>
      {row.isNew ? <span className="newf">new</span> : <span />}
    </a>
  );
}

/** What the Companies count is (#24): Data status's companies with quarterly financials and its
 * companies checked are other counts, so the hover says which this one is. */
export const COMPANIES_COUNTED =
  "Companies you can read: every company with XBRL financials (quarterly or annual) or a stored filing's text. " +
  "Data status counts the ones with quarterly financials, and its checks every company with XBRL figures.";

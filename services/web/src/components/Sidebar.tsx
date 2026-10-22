import type { ReactNode } from "react";

import { ago, day } from "../format";
import type { WatchRow } from "../home/data";
import type { Workspace } from "../home/useWorkspace";
import { href, NAV, type Route } from "../route";
import type { Me } from "../types";
import { Spark } from "./Spark";

const NEXT_FILINGS = 4;

interface Props {
  route: Route;
  me: Me | null;
  workspace: Workspace;
  lastIngested: string | null;
  onAdd: () => void;
  onExport: () => void;
  /** the Workspaces section (workspaces/WorkspaceTree) */
  workspaces?: ReactNode;
}

/** Which nav entry a route belongs to: a company or filing is under Companies. */
function section(route: Route): Route["view"] {
  return route.view === "company" || route.view === "filing" ? "companies" : route.view;
}

export function Sidebar({ route, me, workspace, lastIngested, onAdd, onExport, workspaces }: Props) {
  const { rows, filings, watchlist } = workspace;
  const active = section(route);
  const newCount = (filings ?? []).filter((f) => f.isNew).length;
  const next = rows
    .filter((r) => r.next?.expected_date)
    .sort((a, b) => a.next!.expected_date!.localeCompare(b.next!.expected_date!))
    .slice(0, NEXT_FILINGS);

  return (
    <aside className="side">
      <div className="brand">
        <img src="/favicon.svg" alt="" width={18} height={18} />
        Tributary
        <span className="env" title="Demo users: sign in as any of them from the top bar">
          demo
        </span>
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
              <span className="cnt new" title="New filings since your last visit">
                {newCount}
              </span>
            )}
            {n.view === "companies" && me && (
              <span className="cnt" title="Companies you can read">
                {me.companies.length}
              </span>
            )}
            <kbd>g{n.key}</kbd>
          </a>
        ))}
      </nav>

      <div className="sect">
        <span className="grow">Watchlist{watchlist.state === "ok" ? ` · ${rows.length}` : ""}</span>
        <button onClick={onExport} title="Export CSV" aria-label="Export watchlist CSV">
          ⤓
        </button>
        <button onClick={onAdd} title="Add company" aria-label="Add company to watchlist">
          +
        </button>
      </div>
      <div className="wl">
        {watchlist.state === "loading" && <p className="side-note">Loading watchlist…</p>}
        {watchlist.state === "unavailable" && (
          <p className="side-note">Watchlists are offline; your companies are under Companies.</p>
        )}
        {watchlist.state === "error" && <p className="side-note">Couldn't load the watchlist.</p>}
        {watchlist.state === "ok" && rows.length === 0 && (
          <p className="side-note">Press + to follow a company's filings here.</p>
        )}
        {rows.map((r) => (
          <WatchLink key={r.companyId} row={r} current={route} />
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

      <div className="grow" />
      <div className="fresh">
        {lastIngested ? (
          edgarOpen() ? (
            <>
              <span className="live" />
              Filing feed live · last filing ingested {ago(lastIngested)}
            </>
          ) : (
            <span title="EDGAR accepts filings 6 am–10 pm ET, but filings accepted after 5:30 pm ET mostly reach its feed the next business morning.">
              <span className="live quiet" />
              EDGAR's feed is quiet after 5:30 pm ET and on weekends · last filing {ago(lastIngested)}
            </span>
          )
        ) : (
          "Filing feed: nothing you can read yet"
        )}
        <br />
        {watchlist.state === "ok"
          ? `Watchlist values as of ${new Date(watchlist.data.as_of).toLocaleTimeString([], {
              hour: "2-digit",
              minute: "2-digit",
            })}`
          : "Values appear as they're filed"}
        {me && (
          <>
            <br />
            Signed in as {me.user_id} · {me.companies.length} readable{" "}
            {me.companies.length === 1 ? "company" : "companies"}
          </>
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
    </aside>
  );
}

function WatchLink({ row, current }: { row: WatchRow; current: Route }) {
  const yoy = row.cells.revenue_growth_ttm!;
  const on = current.view === "company" && current.companyId === row.companyId;
  return (
    <a
      href={href({ view: "company", companyId: row.companyId })}
      className={on ? "on" : undefined}
      title={`${row.name} · revenue by quarter, last ${row.revenue.length || 12} quarters`}
    >
      <i className={row.isNew ? "d" : "d off"} title={row.isNew ? "New filing since your last visit" : undefined} />
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

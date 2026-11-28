import { useEffect, useRef } from "react";

import { accountName, readableTotal } from "../access";
import { bellLabel } from "../alerts/alerts";
import { ago, localClock } from "../format";
import { FRESHNESS_EXPLAINED } from "../home/useFeedStatus";
import { href, type Route } from "../route";
import type { AlertUnread, Me } from "../types";
import { MenuButton } from "./MenuButton";
import { Seal } from "./Seal";

interface Props {
  route: Route;
  users: string[];
  user: string | null;
  busy: boolean;
  assistantOpen: boolean;
  onUser: (u: string) => void;
  /** A GitHub or password sign-in: shown by login, with Sign out (which forgets the token). */
  account?: { login: string; onSignOut: () => void };
  /** a demo sandbox: what it covers ("every company", "20 companies") and when it resets ("23h") */
  sandbox?: { covers: string; resetsIn: string; onEnd: () => void } | null;
  onSearch: () => void;
  onAssistant: () => void;
  /** narrow screens: open or close the rail's drawer */
  onMenu?: () => void;
  menuOpen?: boolean;
  /** filing alerts' unread count (F6): undefined while it loads, null without alerts (no bell) */
  alerts?: AlertUnread | null;
  /** the newest filing in the index, whoever's (useFeedStatus) */
  listed?: string | null;
  /** the newest filing's text parsed, among those the user can read */
  lastIngested?: string | null;
  /** the watchlist's values' time */
  valuesAsOf?: string | null;
  me?: Me | null;
}

/** The top bar (.interface-design/system.md §4), 48px and still: the logo; search (Ctrl K opens the
 * palette); the feed's freshness as a status point that opens a popover, linking to Data status; Alerts
 * with a count; the Assistant (Ctrl J); who is signed in, with the way out. The theme is in Settings. */
export function TopBar(props: Props) {
  const { route, users, user, busy, assistantOpen } = props;
  return (
    <header className="top">
      {props.onMenu && (
        <button className="icon-btn menu-btn" aria-label="Menu" aria-expanded={!!props.menuOpen} aria-controls="side-nav" onClick={props.onMenu}>
          <svg viewBox="0 0 18 18" width="18" height="18" aria-hidden="true">
            <path d="M2 4.5h14M2 9h14M2 13.5h14" fill="none" stroke="currentColor" strokeWidth="1.6" />
          </svg>
        </button>
      )}
      <a className="brand" href={href({ view: "home" })} aria-label="Tributary, Home">
        <Seal size="logo" />
        <span className="wordmark">Tributary</span>
      </a>
      <button className="search-box" onClick={props.onSearch} aria-label="Search (Ctrl K)">
        <svg className="search-ic" width="15" height="15" viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="7" cy="7" r="5" fill="none" stroke="currentColor" strokeWidth="1.6" />
          <path d="m11 11 3.5 3.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
        <span className="sb-long">Companies, filings, pages</span>
        <kbd>Ctrl K</kbd>
      </button>
      <div className="top-right">
        <Freshness listed={props.listed ?? null} lastIngested={props.lastIngested ?? null} valuesAsOf={props.valuesAsOf ?? null} me={props.me ?? null} />
        {props.alerts && (() => {
          const reviews = props.alerts.reviews_due ?? 0;
          const count = props.alerts.unread + reviews;
          return (
            <a className={count ? "tb bell on" : "tb bell"} href={href({ view: "alerts" })}
              aria-label={bellLabel(props.alerts.unread, reviews)} title={bellLabel(props.alerts.unread, reviews)}
              aria-current={route.view === "alerts" ? "page" : undefined}>
              <span className="bell-word">Alerts</span>
              {count > 0 && <span className="count" aria-hidden="true">{count > 99 ? "99+" : count}</span>}
            </a>
          );
        })()}
        {route.view !== "ask" && (
          <button className="tb assistant-btn" aria-pressed={assistantOpen} aria-expanded={assistantOpen} aria-controls="assistant-panel"
            onClick={props.onAssistant}>
            Assistant <kbd>Ctrl J</kbd>
          </button>
        )}
        {props.account ? (
          <span className="who">
            <MenuButton className="tb who-chip" count={false} title={`Signed in as ${props.account.login}`}
              label={<><span className="visually-hidden">Signed in as </span><span className="who-name">{props.account.login}</span></>}
              items={[{ label: "Sign out", onSelect: props.account.onSignOut }]} />
          </span>
        ) : users.length > 0 ? (
          <label className="who">
            <span className="visually-hidden">Signed in as</span>
            <select className="who-select" value={user ?? ""} disabled={busy} aria-label="Demo user" onChange={(e) => props.onUser(e.target.value)}>
              {users.map((u) => (
                <option key={u} value={u}>
                  {u}
                </option>
              ))}
            </select>
          </label>
        ) : props.sandbox ? (
          <span className="who">
            <MenuButton className="tb who-chip" count={false}
              title={`A temporary demo sandbox (${props.sandbox.covers}): it and what you save in it are deleted when it resets, in ${props.sandbox.resetsIn}`}
              label={<>Demo sandbox<span className="who-sub"> · resets in {props.sandbox.resetsIn}</span></>}
              items={[{ label: "End demo", onSelect: props.sandbox.onEnd, title: "Leave the demo and go back to the front door; the sandbox is deleted when it resets" }]} />
          </span>
        ) : user ? (
          <span className="who">
            <span className="visually-hidden">Signed in as </span>
            <span className="who-name">{user}</span>
          </span>
        ) : null}
      </div>
    </header>
  );
}

/** The feed's freshness (§4's status point): a point, green while EDGAR's feed is in its hours, and when
 * the newest filing was listed; it opens what that means, linking to Data status. Words from the
 * freshness vocabulary only (listed, parsed): never "live". */
function Freshness({ listed, lastIngested, valuesAsOf, me }: { listed: string | null; lastIngested: string | null; valuesAsOf: string | null; me: Me | null }) {
  const ref = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const away = (e: PointerEvent) => {
      const d = ref.current;
      if (d?.open && !d.contains(e.target as Node)) d.open = false;
    };
    const esc = (e: KeyboardEvent) => {
      const d = ref.current;
      if (e.key === "Escape" && d?.open) {
        d.open = false;
        d.querySelector("summary")?.focus();
      }
    };
    document.addEventListener("pointerdown", away, true);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("pointerdown", away, true);
      document.removeEventListener("keydown", esc);
    };
  }, []);
  const open = edgarOpen();
  return (
    <details className="fresh" ref={ref} title={FRESHNESS_EXPLAINED}>
      <summary className="tb">
        <span className={listed && open ? "live" : "live quiet"} aria-hidden="true" />
        {/* what was listed, said (Marek 48: a bare "Listed 1 h ago" read as nothing) */}
        {listed ? <><span className="fresh-long">Newest filing listed</span><span className="fresh-short">Listed</span> <span className="fresh-ago">{ago(listed)}</span></> : "Data freshness"}
      </summary>
      <div className="pop fresh-pop">
        <p>
          {listed ? <>Newest filing listed {ago(listed)}</> : "The feed's time isn't known yet"}
          {listed && !open && <><br />EDGAR's feed slows after 5:30 pm ET and on weekends</>}
        </p>
        <p className="pencil">
          {lastIngested ? `Newest filing text parsed ${ago(lastIngested)}` : "Filings: nothing you can read yet"}
          <br />
          {valuesAsOf ? `Watchlist values as of ${localClock(valuesAsOf)}` : "Values appear as they're filed"}
          {me && (
            <>
              <br />
              Signed in as {accountName(me)}
              <br />
              {readableTotal(me).toLocaleString("en-US")} readable {readableTotal(me) === 1 ? "company" : "companies"}
            </>
          )}
        </p>
        <p><a href={href({ view: "status" })}>Data status →</a></p>
      </div>
    </details>
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

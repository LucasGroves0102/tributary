import { bellLabel } from "../alerts/alerts";
import { UNNAMED } from "../companies/directory";
import { href, NAV, type Route } from "../route";
import { THEME_WORD, type ThemeChoice } from "../theme";
import { MenuButton } from "./MenuButton";
import type { AlertUnread } from "../types";

interface Props {
  route: Route;
  /** "Nvidia (NVDA)" (companies/directory.ts) */
  companyName: (companyId: string) => string | null;
  /** "NVDA": beside a filing, where the room goes to the filing */
  companyShort?: (companyId: string) => string;
  /** what the reader has open: "10-K filed Feb 21, 2026" */
  filingLabel?: string | null;
  users: string[];
  user: string | null;
  busy: boolean;
  /** Light, Dark or System (the visitor's): the button says which, and moves to the next */
  theme: ThemeChoice;
  assistantOpen: boolean;
  onUser: (u: string) => void;
  /** A GitHub sign-in: shown by login, with Sign out (which forgets the token). */
  account?: { login: string; onSignOut: () => void };
  onSearch: () => void;
  /** the three themes by name (Riley 72: the button was a blind cycle) */
  onTheme: (choice: ThemeChoice) => void;
  /** a demo sandbox's way out: forget it and go back to the front door (Riley 14: there was none) */
  onEndDemo?: () => void;
  onAssistant: () => void;
  /** narrow screens: open or close the sidebar drawer */
  onMenu?: () => void;
  menuOpen?: boolean;
  /** filing alerts' unread count (F6): undefined while it loads, null without alerts (no bell) */
  alerts?: AlertUnread | null;
}

export function TopBar(props: Props) {
  const { route, users, user, busy, theme, assistantOpen } = props;
  return (
    <header className="top">
      {props.onMenu && (
        <button className="btn ghost sm menu-btn" aria-label="Menu" aria-expanded={!!props.menuOpen}
          aria-controls="side-nav" onClick={props.onMenu}>
          ☰
        </button>
      )}
      <Breadcrumb route={route} companyName={props.companyName} companyShort={props.companyShort} filingLabel={props.filingLabel ?? null} />
      <button className="search-box" onClick={props.onSearch} aria-label="Search (Ctrl K)">
        <svg className="search-ic" width="13" height="13" viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="7" cy="7" r="5" fill="none" stroke="currentColor" strokeWidth="1.6" />
          <path d="M11 11l4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
        <span className="sb-long">Search companies, filings, pages, actions</span>
        <span className="sb-short">Search</span>
        <span className="grow" />
        <kbd>Ctrl K</kbd>
      </button>
      <span className="grow" />
      {props.account ? (
        <span className="who">
          <span className="faint">Signed in as</span> <span className="who-name">{props.account.login}</span>
          <button className="btn ghost sm" onClick={props.account.onSignOut}>
            Sign out
          </button>
        </span>
      ) : users.length > 0 ? (
        <label className="who">
          <span className="faint">Signed in as</span>
          <select
            value={user ?? ""}
            disabled={busy}
            aria-label="Demo user"
            onChange={(e) => props.onUser(e.target.value)}
          >
            {users.map((u) => (
              <option key={u} value={u}>
                {u}
              </option>
            ))}
          </select>
        </label>
      ) : (
        user && (
          <span className="who">
            <span className="faint">Signed in to</span> <span className="who-name">{user}</span>
            {props.onEndDemo && (
              <button className="btn ghost sm end-demo" onClick={props.onEndDemo} title="Leave the demo and go back to the front door; the sandbox is deleted when it resets">
                End demo
              </button>
            )}
          </span>
        )
      )}
      {props.alerts && (
        <a className={props.alerts.unread ? "btn ghost sm bell on" : "btn ghost sm bell"} href={href({ view: "alerts" })}
          aria-label={bellLabel(props.alerts.unread)} title={bellLabel(props.alerts.unread)}
          aria-current={route.view === "alerts" ? "page" : undefined}>
          <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
            <path d="M8 1.5a4 4 0 0 0-4 4v2.6L2.6 11h10.8L12 8.1V5.5a4 4 0 0 0-4-4zM6.3 12.5a1.8 1.8 0 0 0 3.4 0"
              fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" strokeLinecap="round" />
          </svg>
          <span className="bell-word">Alerts</span>
          {props.alerts.unread > 0 && <span className="count" aria-hidden="true">{props.alerts.unread > 99 ? "99+" : props.alerts.unread}</span>}
        </a>
      )}
      <MenuButton className="btn ghost sm theme-btn more-btn" count={false}
        title={`Theme: ${THEME_WORD[theme].toLowerCase()}${theme === "system" ? " (your device's setting)" : ""}`}
        label={<><span className="theme-k">Theme</span> {THEME_WORD[theme]}</>}
        items={(["light", "dark", "system"] as const).map((c) => ({
          label: c === "system" ? "System (your device's setting)" : THEME_WORD[c], checked: theme === c, onCheckedChange: () => props.onTheme(c),
        }))} />
      {route.view !== "ask" && (
        <button
          className="btn sm assistant-btn"
          aria-pressed={assistantOpen}
          onClick={props.onAssistant}
        >
          Assistant <span className="kbd">Ctrl J</span>
        </button>
      )}
    </header>
  );
}

function Breadcrumb({
  route,
  companyName,
  companyShort,
  filingLabel,
}: {
  route: Route;
  companyName: (companyId: string) => string | null;
  companyShort?: (companyId: string) => string;
  filingLabel: string | null;
}) {
  if (route.view === "company" || route.view === "filing") {
    const name = companyName(route.companyId) ?? UNNAMED;
    return (
      <nav className="crumb" aria-label="Breadcrumb">
        <a href={href({ view: "companies" })}>Companies</a>
        <span className="sep">/</span>
        {route.view === "company" ? (
          <span aria-current="page">{name}</span>
        ) : (
          <>
            <a href={href({ view: "company", companyId: route.companyId })} title={name}>{companyShort?.(route.companyId) ?? name}</a>
            <span className="sep">/</span>
            <span aria-current="page">{filingLabel ?? "Filing"}</span>
          </>
        )}
      </nav>
    );
  }
  if (route.view === "search") {
    return (
      <nav className="crumb" aria-label="Breadcrumb">
        <span aria-current="page">Search filings{route.search ? `: “${route.search.q}”` : ""}</span>
      </nav>
    );
  }
  const label = route.view === "about" ? "What Tributary covers"
    : route.view === "status" ? "Data status"
    : route.view === "api" ? "Using the API"
    : route.view === "settings" ? "Settings"
    : route.view === "alerts" ? "Alerts"
    : route.view === "workspace" ? "Workspace"
    : route.view === "report" ? "Report"
    : route.view === "thesis" ? "Thesis"
    : route.view === "model" ? "Model"
    : (NAV.find((n) => n.view === route.view)?.label ?? "Home");
  return (
    <nav className="crumb" aria-label="Breadcrumb">
      <span aria-current="page">{label}</span>
    </nav>
  );
}

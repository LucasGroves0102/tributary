import { href, NAV, type Route } from "../route";
import type { Theme } from "../theme";

interface Props {
  route: Route;
  companyName: (companyId: string) => string | null;
  users: string[];
  user: string | null;
  busy: boolean;
  theme: Theme;
  assistantOpen: boolean;
  onUser: (u: string) => void;
  onSearch: () => void;
  onTheme: () => void;
  onAssistant: () => void;
  /** narrow screens: open or close the sidebar drawer */
  onMenu?: () => void;
  menuOpen?: boolean;
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
      <Breadcrumb route={route} companyName={props.companyName} />
      <button className="search-box" onClick={props.onSearch} aria-label="Search (Ctrl K)">
        <span>Search companies, filings, pages, actions</span>
        <span className="grow" />
        <kbd>Ctrl K</kbd>
      </button>
      <span className="grow" />
      {users.length > 0 ? (
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
            <span className="faint">Signed in to</span> {user}
          </span>
        )
      )}
      <button className="btn ghost sm" onClick={props.onTheme}>
        {theme === "dark" ? "Light" : "Dark"}
      </button>
      {route.view !== "ask" && (
        <button
          className={assistantOpen ? "btn sm" : "btn sm primary"}
          aria-pressed={assistantOpen}
          onClick={props.onAssistant}
        >
          ✦ Assistant <span className="kbd">Ctrl J</span>
        </button>
      )}
    </header>
  );
}

function Breadcrumb({
  route,
  companyName,
}: {
  route: Route;
  companyName: (companyId: string) => string | null;
}) {
  if (route.view === "company" || route.view === "filing") {
    const name = companyName(route.companyId) ?? `CIK ${route.companyId}`;
    return (
      <nav className="crumb" aria-label="Breadcrumb">
        <a href={href({ view: "companies" })}>Companies</a>
        <span className="sep">/</span>
        {route.view === "company" ? (
          <span aria-current="page">{name}</span>
        ) : (
          <>
            <a href={href({ view: "company", companyId: route.companyId })}>{name}</a>
            <span className="sep">/</span>
            <span aria-current="page">Filing {route.accessionNo}</span>
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
  const label = route.view === "about" ? "What Tributary covers" : (NAV.find((n) => n.view === route.view)?.label ?? "Home");
  return (
    <nav className="crumb" aria-label="Breadcrumb">
      <span aria-current="page">{label}</span>
    </nav>
  );
}

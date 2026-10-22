import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { demoUsers, login, me as fetchMe, watchlistCsv } from "./api";
import { AssistantPanel } from "./assistant/AssistantPanel";
import { companiesChart, companyChart, type Transform } from "./chart/model";
import { useChart } from "./chart/useChart";
import { uiContext } from "./assistant/actions";
import { pageContext } from "./assistant/context";
import { useAssistant } from "./assistant/useAssistant";
import { useUiActions } from "./assistant/useUiActions";
import { FilingReader } from "./components/explore/FilingReader";
import { Palette, WATCH_PREFIX, type PaletteActions, type PaletteCompany } from "./components/Palette";
import { Sidebar } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
import { download } from "./download";
import { lastIngested } from "./home/data";
import { useWorkspace } from "./home/useWorkspace";
import { ChartBuilder } from "./pages/ChartBuilder";
import { Companies } from "./pages/Companies";
import { Home } from "./pages/Home";
import { Company } from "./pages/Company";
import { Compare } from "./pages/Compare";
import { Search } from "./pages/Search";
import { go, parse, useRoute } from "./route";
import { useShortcuts } from "./shortcuts";
import { load, save } from "./storage";
import { useTheme } from "./theme";
import type { Me } from "./types";

const DRAWER_KEY = "tributary.assistant";
const DRAWER_FITS_PX = 1200;

export function App() {
  const [users, setUsers] = useState<string[]>([]);
  const [user, setUser] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  // open by default where it fits beside the page; below that it floats over it (styles.css)
  const [drawerOpen, setDrawerOpen] = useState(
    () => (load(DRAWER_KEY) ?? (window.innerWidth > DRAWER_FITS_PX ? "open" : "closed")) === "open",
  );
  const [palette, setPalette] = useState<string | null>(null);
  const [focusTick, setFocusTick] = useState(0);
  const input = useRef<HTMLTextAreaElement>(null);
  const route = useRoute();
  const [theme, toggleTheme] = useTheme(user);
  const workspace = useWorkspace(token, user, me?.companies.map((c) => c.company_id));
  const [chart, setChart] = useChart(user);
  // read when the assistant sends a question or applies an action, so always the latest
  const chartRef = useRef(chart);
  chartRef.current = chart;
  const watchedRef = useRef<Set<string> | null>(null);
  const nameRef = useRef<(id: string) => string | null>(() => null);
  const uiActions = useUiActions(user, {
    setChart,
    isWatched: (id) => watchedRef.current?.has(id) ?? false,
    watch: workspace.watch,
    unwatch: workspace.unwatch,
    onError: setError,
  });
  const assistant = useAssistant(token, user, {
    context: () => uiContext(parse(window.location.hash), chartRef.current, nameRef.current),
    onAction: uiActions.apply,
  });

  useEffect(() => {
    demoUsers()
      .then((u) => {
        setUsers(u);
        setUser(u[0] ?? null);
      })
      .catch(() => setError("Can't reach the Query API."));
  }, []);

  useEffect(() => {
    if (!user) return;
    setMe(null);
    setToken(null);
    let cancelled = false;
    login(user)
      .then(async (t) => {
        const m = await fetchMe(t);
        if (!cancelled) {
          setToken(t);
          setMe(m);
        }
      })
      .catch(() => !cancelled && setError(`Can't sign in as ${user}.`));
    return () => {
      cancelled = true;
    };
  }, [user]);

  useEffect(() => {
    if (focusTick) input.current?.focus();
  }, [focusTick]);

  const setDrawer = useCallback((open: boolean) => {
    setDrawerOpen(open);
    save(DRAWER_KEY, open ? "open" : "closed");
  }, []);

  /** Open the drawer (except on Ask, which is the assistant) and put the cursor in it. */
  const focusAssistant = useCallback(() => {
    if (route.view !== "ask") setDrawer(true);
    setFocusTick((n) => n + 1);
  }, [route.view, setDrawer]);

  /** Compare's "Chart these" and the peers tab: one line per company for a metric. */
  const chartMany = useCallback(
    (ids: string[], metric: string, transform: Transform, title: string) => {
      setChart((s) => companiesChart(s, ids, metric, transform, title));
      go({ view: "chart" });
    },
    [setChart],
  );

  const exportWatchlist = useCallback(() => {
    if (!token) return;
    watchlistCsv(token)
      .then((blob) => download("watchlist.csv", blob))
      .catch(() => setError("Couldn't export the watchlist. Try again."));
  }, [token]);

  useShortcuts((s) => {
    switch (s.type) {
      case "palette":
        setPalette((p) => (p === null ? "" : null));
        break;
      case "assistant":
        if (route.view === "ask") focusAssistant();
        else if (drawerOpen) setDrawer(false);
        else focusAssistant();
        break;
      case "focus-assistant":
        focusAssistant();
        break;
      case "go":
        setPalette(null);
        go({ view: s.view });
    }
  });

  const names = useMemo(() => {
    const out = new Map<string, string>();
    for (const r of workspace.rows) out.set(r.companyId, r.name);
    for (const c of me?.companies ?? []) if (c.name) out.set(c.company_id, c.name);
    return out;
  }, [me, workspace.rows]);
  const watched = useMemo(
    () =>
      workspace.watchlist.state === "ok"
        ? new Set(workspace.watchlist.data.companies.map((c) => c.company_id))
        : null,
    [workspace.watchlist],
  );
  const companyName = useCallback((id: string) => names.get(id) ?? null, [names]);
  watchedRef.current = watched;
  nameRef.current = companyName;
  const context = useMemo(() => pageContext(route, companyName), [route, companyName]);

  const paletteCompanies = useMemo((): PaletteCompany[] => {
    const out = new Map<string, PaletteCompany>();
    for (const r of workspace.rows) {
      out.set(r.companyId, {
        companyId: r.companyId,
        name: r.name,
        sub: [r.ticker, "watchlist"].filter(Boolean).join(" · "),
      });
    }
    for (const c of me?.companies ?? []) {
      if (!out.has(c.company_id)) {
        out.set(c.company_id, {
          companyId: c.company_id,
          name: c.name ?? c.company_id,
          sub: `company · ${c.tiers.join(", ")}`,
        });
      }
    }
    return [...out.values()];
  }, [me, workspace.rows]);

  const actions = useMemo(
    (): PaletteActions => ({
      go: (view) => go({ view }),
      company: (companyId) => go({ view: "company", companyId }),
      watch: (companyId) => {
        workspace.watch(companyId).catch(() => setError("Couldn't add that company. Try again."));
      },
      ask: (q) => {
        focusAssistant();
        void assistant.submit(q);
      },
      toggleTheme,
      exportWatchlist,
      openAssistant: focusAssistant,
      searchFilings: (q) => go({ view: "search", search: { q } }),
    }),
    [workspace, focusAssistant, assistant, toggleTheme, exportWatchlist],
  );

  const showDrawer = drawerOpen && route.view !== "ask";
  const ingested = lastIngested(workspace.latest ?? []);
  const panelProps = {
    assistant,
    onUndo: (id: number) => void uiActions.undo(id),
    undone: uiActions.undone,
    context,
    signedIn: token !== null,
    inputRef: input,
  };

  return (
    <>
      <div className={showDrawer ? "app with-ai" : "app"}>
        <Sidebar
          route={route}
          me={me}
          workspace={workspace}
          lastIngested={ingested}
          onAdd={() => setPalette(WATCH_PREFIX)}
          onExport={exportWatchlist}
        />
        <div className="main">
          <TopBar
            route={route}
            companyName={companyName}
            users={users}
            user={user}
            busy={assistant.busy}
            theme={theme}
            assistantOpen={showDrawer}
            onUser={setUser}
            onSearch={() => setPalette("")}
            onTheme={toggleTheme}
            onAssistant={() => (drawerOpen ? setDrawer(false) : focusAssistant())}
          />
          {error && (
            <div className="banner" role="alert">
              {error}
              <button className="btn sm ghost" onClick={() => setError(null)}>
                Dismiss
              </button>
            </div>
          )}
          <main className="content">
            {!token ? (
              <p className="page muted">Signing in…</p>
            ) : route.view === "home" ? (
              <Home
                workspace={workspace}
                lastIngested={ingested}
                onAdd={() => setPalette(WATCH_PREFIX)}
                onExport={exportWatchlist}
              />
            ) : route.view === "companies" ? (
              <Companies
                key={user}
                token={token}
                screen={route.screen ?? {}}
                watched={watched}
                onWatch={(id, on) => (on ? workspace.watch(id) : workspace.unwatch(id))}
                onChart={(id, name) => {
                  setChart((s) => companyChart(s, id, name));
                  go({ view: "chart" });
                }}
              />
            ) : route.view === "company" ? (
              <Company
                key={`${user}-${route.companyId}`}
                token={token}
                companyId={route.companyId}
                tab={route.tab ?? "overview"}
                watched={watched ? watched.has(route.companyId) : null}
                onWatch={(on) =>
                  (on ? workspace.watch(route.companyId) : workspace.unwatch(route.companyId)).catch(() =>
                    setError("Couldn't update the watchlist. Try again."),
                  )
                }
                onChart={(name) => {
                  setChart((s) => companyChart(s, route.companyId, name));
                  go({ view: "chart" });
                }}
                onChartMany={chartMany}
                onAsk={(q) => {
                  assistant.setDraft(q);
                  focusAssistant();
                }}
              />
            ) : route.view === "filing" ? (
              <FilingReader
                key={user}
                token={token}
                companyId={route.companyId}
                accessionNo={route.accessionNo}
                sectionKey={route.section}
                at={route.at}
                q={route.q}
                name={companyName(route.companyId)}
                onAsk={(q) => {
                  assistant.setDraft(q);
                  focusAssistant();
                }}
              />
            ) : route.view === "search" ? (
              <Search key={user} token={token} search={route.search} companies={me?.companies ?? []} />
            ) : route.view === "chart" ? (
              <ChartBuilder key={user} token={token} chart={chart} setChart={setChart} />
            ) : route.view === "ask" ? (
              <div className="page ask-page">
                <div className="row">
                  <h1 className="grow">Ask</h1>
                </div>
                <AssistantPanel variant="page" {...panelProps} />
                <p className="faint small">
                  The same assistant as the side panel, full width. It answers only from data{" "}
                  {user ?? "you"} can read, with the filing behind every number.
                </p>
              </div>
            ) : (
              <Compare
                key={user}
                token={token}
                compare={route.compare ?? {}}
                onChart={chartMany}
              />
            )}
          </main>
        </div>
        {showDrawer && (
          <AssistantPanel variant="drawer" {...panelProps} onClose={() => setDrawer(false)} />
        )}
      </div>
      {!showDrawer && route.view !== "ask" && (
        <button className="btn primary fab" onClick={focusAssistant}>
          ✦ Ask the assistant
        </button>
      )}
      {palette !== null && (
        <Palette
          key={palette}
          token={token}
          initial={palette}
          companies={paletteCompanies}
          actions={actions}
          onClose={() => setPalette(null)}
        />
      )}
    </>
  );
}

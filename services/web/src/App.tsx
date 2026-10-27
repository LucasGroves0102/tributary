import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, demoUsers, login, me as fetchMe, watchlistCsv } from "./api";
import { AssistantPanel } from "./assistant/AssistantPanel";
import { metricLabel } from "./chart/metrics";
import { companiesChart, companyChart, type Transform } from "./chart/model";
import { useChart } from "./chart/useChart";
import { uiContext } from "./assistant/actions";
import { pageContext } from "./assistant/context";
import { useAssistant } from "./assistant/useAssistant";
import { useUiActions } from "./assistant/useUiActions";
import { NotInDemo } from "./components/NotInDemo";
import { Palette, WATCH_PREFIX, type PaletteActions, type PaletteCompany } from "./components/Palette";
import { ShortcutSheet } from "./components/ShortcutSheet";
import { Sidebar } from "./components/Sidebar";
import { Skeleton } from "./components/States";
import { TopBar } from "./components/TopBar";
import { download } from "./download";
import { lastIngested, watchColumns } from "./home/data";
import { RecentNotes } from "./home/RecentNotes";
import { useWorkspace } from "./home/useWorkspace";
import { Home } from "./pages/Home";
import {
  About,
  ChartBuilder,
  Companies,
  Company,
  Compare,
  FilingReader,
  prefetchScreens,
  ReportView,
  Search,
  WorkspacePage,
} from "./pages/lazy";
import { readableCount, Welcome } from "./pages/Welcome";
import { go, parse, useRoute } from "./route";
import { resolveTicker } from "./palette/commands";
import { useShortcuts } from "./shortcuts";
import { inScope, ScopeContext, scopeOf } from "./scope";
import { clearSession, loadSession, pickerEnabled, resetsIn, type Session } from "./session";
import { load, save } from "./storage";
import { useReportWriter } from "./reports/useReportWriter";
import { useTheme } from "./theme";
import { ColumnPicker, HomeLayoutEditor } from "./views/Editors";
import { columnSpecs, DEFAULT_SCREENER_COLUMNS, DEFAULT_WATCHLIST_COLUMNS } from "./views/model";
import { useViews } from "./views/useViews";
import type { Me } from "./types";
import { itemRoute, restoreChartState, type Current } from "./workspaces/model";
import { Recent } from "./workspaces/Recent";
import { SaveBar } from "./workspaces/SaveBar";
import { useSaved } from "./workspaces/useSaved";
import { WorkspaceTree } from "./workspaces/WorkspaceTree";

const DRAWER_KEY = "tributary.assistant";
const DRAWER_FITS_PX = 1200;

const SANDBOX_EXPIRED = "Your demo sandbox expired and was reset. Start a new one.";

interface Props {
  /** The named demo-user picker (alice, bob, …) instead of "Try the demo". Default: the dev
   * flag (session.ts `pickerEnabled`). */
  picker?: boolean;
}

export function App(props: Props = {}) {
  const [picker] = useState(() => props.picker ?? pickerEnabled());
  const [users, setUsers] = useState<string[]>([]);
  const [user, setUser] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  // "Try the demo": the sandbox signed in, or the front door (with an optional notice)
  const [session, setSession] = useState<Session | null>(null);
  const [frontDoor, setFrontDoor] = useState<{ notice: string | null } | null>(null);
  const [clock, setClock] = useState(() => Date.now());
  // a demo sandbox lists only its own companies everywhere (scope.ts); others see the index
  const scope = useMemo(() => scopeOf(session), [session]);
  // open by default where it fits beside the page; below that it floats over it (styles.css)
  const [drawerOpen, setDrawerOpen] = useState(
    () => (load(DRAWER_KEY) ?? (window.innerWidth > DRAWER_FITS_PX ? "open" : "closed")) === "open",
  );
  const [palette, setPalette] = useState<string | null>(null);
  const [help, setHelp] = useState(false);
  const [focusTick, setFocusTick] = useState(0);
  const input = useRef<HTMLTextAreaElement>(null);
  const route = useRoute();
  // narrow screens: the sidebar is a drawer behind the Menu button (styles.css), closed on
  // every navigation and on Escape
  const [navOpen, setNavOpen] = useState(false);
  useEffect(() => setNavOpen(false), [route]);
  // the other screens' code, once signed in and idle (pages/lazy.ts)
  const signedIn = token !== null;
  useEffect(() => {
    if (signedIn) prefetchScreens();
  }, [signedIn]);
  useEffect(() => {
    if (!navOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setNavOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navOpen]);
  const [theme, toggleTheme] = useTheme(user);
  const views = useViews(token, user);
  const [customizing, setCustomizing] = useState(false);
  const watchCols = useMemo(() => watchColumns(views.watchlistColumns, views.options), [views.watchlistColumns, views.options]);
  const screenCols = useMemo(() => columnSpecs(views.screenerColumns, views.options), [views.screenerColumns, views.options]);
  const workspace = useWorkspace(token, user, me?.companies.map((c) => c.company_id), watchCols);
  const saved = useSaved(token, user);
  const reload = saved.reload;
  // a report saved: refresh the lists, and show it if its workspace is still on screen
  const writer = useReportWriter(token, user, useCallback((itemId: string, workspaceId: string) => {
    reload();
    const r = parse(window.location.hash);
    if (r.view === "workspace" && r.workspaceId === workspaceId) go({ view: "report", itemId });
  }, [reload]));
  useEffect(() => {
    if (views.error) setError(views.error);
  }, [views.error]);
  const [chart, setChart] = useChart(user);
  // read when the assistant sends a question or applies an action, so always the latest
  const chartRef = useRef(chart);
  chartRef.current = chart;
  const watchedRef = useRef<Set<string> | null>(null);
  const nameRef = useRef<(id: string) => string | null>(() => null);
  const uiActions = useUiActions(user, {
    setChart,
    chart: () => chartRef.current,
    saveTo: (current, name, ws) => saved.saveAs(current, name, { newWorkspace: ws }).then((i) => i.item_id),
    deleteSaved: saved.deleteItem,
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
    if (!picker) return;
    demoUsers()
      .then((u) => {
        setUsers(u);
        setUser(u[0] ?? null);
      })
      .catch(() => setError("Can't reach the Query API."));
  }, [picker]);

  const signIn = useCallback((s: Session, m: Me | null) => {
    setSession(s);
    setUser(s.userId);
    setToken(s.token);
    setMe(m);
    setFrontDoor(null);
    setClock(Date.now());
  }, []);

  const signOut = useCallback((notice: string | null) => {
    clearSession();
    setSession(null);
    setUser(null);
    setToken(null);
    setMe(null);
    setFrontDoor({ notice });
  }, []);

  // resume a sandbox from an earlier visit, else the front door
  useEffect(() => {
    if (picker) return;
    const stored = loadSession();
    if (!stored) {
      setFrontDoor({ notice: null });
      return;
    }
    let cancelled = false;
    fetchMe(stored.token)
      .then((m) => !cancelled && signIn(stored, m))
      .catch((e) => {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 401) signOut(SANDBOX_EXPIRED);
        else signIn(stored, null);
      });
    return () => {
      cancelled = true;
    };
  }, [picker, signIn, signOut]);

  // the sandbox's clock: the banner's countdown, and back to the front door when it expires
  useEffect(() => {
    if (!session) return;
    const id = window.setInterval(() => setClock(Date.now()), 60_000);
    return () => window.clearInterval(id);
  }, [session]);
  useEffect(() => {
    if (session && Date.parse(session.expiresAt) <= clock) signOut(SANDBOX_EXPIRED);
  }, [session, clock, signOut]);

  // continued before every grant had propagated (or /v1/me failed): refresh until they have
  useEffect(() => {
    if (!session) return;
    if (me && readableCount(me, new Set(session.companies)) >= session.companies.length) return;
    const id = window.setTimeout(() => {
      fetchMe(session.token)
        .then(setMe)
        .catch(() => undefined);
    }, 3000);
    return () => window.clearTimeout(id);
  }, [session, me]);

  useEffect(() => {
    if (!user || !picker) return;
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
  }, [user, picker]);

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
      saved.close("chart"); // a new chart, not an edit of the saved one
      go({ view: "chart" });
    },
    [setChart, saved],
  );

  /** Opens a saved item: its state on its page (the page then reads data as usual). */
  const openSaved = useCallback(
    (itemId: string) => {
      saved
        .openItem(itemId)
        .then((item) => {
          if (item.kind === "report") return go({ view: "report", itemId: item.item_id });
          if (item.kind === "chart") setChart(() => restoreChartState(item.state));
          go(itemRoute(item.kind, item.state));
        })
        .catch(() => setError("Couldn't open that item. It may have been deleted; the list is refreshed."));
    },
    [saved, setChart],
  );

  const exportWatchlist = useCallback(() => {
    if (!token) return;
    watchlistCsv(token, views.watchlistColumns)
      .then((blob) => download("watchlist.csv", blob))
      .catch(() => setError("Couldn't export the watchlist. Try again."));
  }, [token, views.watchlistColumns]);

  useShortcuts((s) => {
    switch (s.type) {
      case "palette":
        setPalette((p) => (p === null ? "" : null));
        break;
      case "help":
        setHelp((h) => !h);
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
  const filingsFor = useMemo(
    () => new Set((me?.companies ?? []).filter((c) => c.tiers.includes("filings")).map((c) => c.company_id)),
    [me],
  );
  const financialsFor = useMemo(
    () => new Set((me?.companies ?? []).filter((c) => c.tiers.includes("financials")).map((c) => c.company_id)),
    [me],
  );
  watchedRef.current = watched;
  nameRef.current = companyName;
  const context = useMemo(
    () => pageContext(route, companyName, (id) => inScope(scope, id)),
    [route, companyName, scope],
  );

  const paletteCompanies = useMemo((): PaletteCompany[] => {
    const out = new Map<string, PaletteCompany>();
    for (const r of workspace.rows) {
      if (!inScope(scope, r.companyId)) continue;
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
  }, [me, workspace.rows, scope]);

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
      compareTickers: (tickers) => {
        if (!token) return;
        Promise.all(tickers.map((t) => resolveTicker(token, t, scope !== null)))
          .then((found) => {
            const missing = tickers.filter((_, i) => !found[i]);
            if (missing.length) return setError(noTicker(missing, scope !== null));
            const [target, ...peers] = found.map((f) => f!.companyId);
            go({ view: "compare", compare: { target, peers } });
          })
          .catch(() => setError("Couldn't look those tickers up. Try again."));
      },
      chartTicker: (ticker, metric) => {
        if (!token) return;
        resolveTicker(token, ticker, scope !== null)
          .then((found) => {
            if (!found) return setError(noTicker([ticker], scope !== null));
            chartMany([found.companyId], metric, "ttm", `${ticker} ${metricLabel(metric).toLowerCase()}`);
          })
          .catch(() => setError("Couldn't look that ticker up. Try again."));
      },
    }),
    [workspace, focusAssistant, assistant, toggleTheme, exportWatchlist, token, chartMany, scope],
  );

  const saveBar = (current: Current) =>
    saved.list.state === "unavailable" ? null : (
      <SaveBar current={current} saved={saved} label={(id) => companyName(id) ?? id} />
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

  if (!picker && !token && frontDoor) {
    return (
      <Welcome
        notice={frontDoor.notice}
        onReady={(s, m) => {
          signIn(s, m);
          go({ view: "home" });
        }}
      />
    );
  }

  return (
    <ScopeContext.Provider value={scope}>
      <div className={showDrawer ? "app with-ai" : "app"}>
        <Sidebar
          open={navOpen}
          route={route}
          me={me}
          workspace={workspace}
          lastIngested={ingested}
          onAdd={() => setPalette(WATCH_PREFIX)}
          onExport={exportWatchlist}
          onShortcuts={() => setHelp(true)}
          workspaces={token ? <WorkspaceTree saved={saved} onOpen={openSaved} onError={setError} /> : null}
        />
        {navOpen && <div className="scrim" aria-hidden="true" onClick={() => setNavOpen(false)} />}
        <div className="main">
          <TopBar
            menuOpen={navOpen}
            onMenu={() => setNavOpen((o) => !o)}
            route={route}
            companyName={companyName}
            users={users}
            user={session ? "demo sandbox" : user}
            busy={assistant.busy}
            theme={theme}
            assistantOpen={showDrawer}
            onUser={setUser}
            onSearch={() => setPalette("")}
            onTheme={toggleTheme}
            onAssistant={() => (drawerOpen ? setDrawer(false) : focusAssistant())}
          />
          {session && (
            <div className="sandbox-banner" role="note">
              Demo sandbox: {session.companies.length} companies · resets in{" "}
              {resetsIn(session.expiresAt, clock)}
            </div>
          )}
          {error && (
            <div className="banner" role="alert">
              {error}
              <button className="btn sm ghost" onClick={() => setError(null)}>
                Dismiss
              </button>
            </div>
          )}
          <main className="content">
            <Suspense fallback={<div className="page"><Skeleton lines={6} label="Loading the page" /></div>}>
            {!token ? (
              <p className="page muted">Signing in…</p>
            ) : route.view === "home" ? (
              <Home
                workspace={workspace}
                lastIngested={ingested}
                onAdd={() => setPalette(WATCH_PREFIX)}
                onExport={exportWatchlist}
                recent={saved.list.state === "unavailable" ? null : <Recent recent={saved.recent} onOpen={openSaved} />}
                notes={<RecentNotes token={token} name={companyName} filingsFor={filingsFor} />}
                layout={views.home}
                onCustomize={views.editable ? () => setCustomizing((c) => !c) : undefined}
                editor={customizing && views.editable ? (
                  <HomeLayoutEditor
                    cards={views.home}
                    onChange={views.setHome}
                    onReset={() => views.reset("home")}
                    onClose={() => setCustomizing(false)}
                  />
                ) : null}
                columnPicker={views.editable ? (
                  <ColumnPicker
                    table="watchlist"
                    columns={views.watchlistColumns}
                    options={views.options}
                    defaults={DEFAULT_WATCHLIST_COLUMNS}
                    max={views.max.watchlist}
                    onChange={(c) => views.setColumns("watchlist", c)}
                    onReset={() => views.reset("watchlist")}
                  />
                ) : null}
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
                  saved.close("chart");
                  go({ view: "chart" });
                }}
                saveBar={saveBar({ kind: "screen", screen: route.screen ?? {} })}
                columns={screenCols}
                columnPicker={views.editable ? (
                  <ColumnPicker
                    table="screener"
                    columns={views.screenerColumns}
                    options={views.options}
                    defaults={DEFAULT_SCREENER_COLUMNS}
                    max={views.max.screener}
                    onChange={(c) => views.setColumns("screener", c)}
                    onReset={() => views.reset("screener")}
                  />
                ) : null}
              />
            ) : (route.view === "company" || route.view === "filing") && scope &&
              !inScope(scope, route.companyId) ? (
              <NotInDemo key={route.companyId} token={token} companyId={route.companyId} scope={scope} />
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
                  saved.close("chart");
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
                onRead={(section) => saved.recordFiling(route.companyId, route.accessionNo, section)}
              />
            ) : route.view === "search" ? (
              <Search key={user} token={token} search={route.search} companies={me?.companies ?? []} />
            ) : route.view === "chart" ? (
              <ChartBuilder
                key={user}
                token={token}
                chart={chart}
                setChart={setChart}
                saveBar={saveBar({ kind: "chart", chart })}
              />
            ) : route.view === "ask" ? (
              <div className="page ask-page">
                <div className="row">
                  <h1 className="grow">Ask</h1>
                </div>
                <AssistantPanel variant="page" {...panelProps} />
                <p className="faint small">
                  The same assistant as the side panel, full width. It answers only from data{" "}
                  {session ? "your sandbox" : (user ?? "you")} can read, with the filing behind every
                  number.
                </p>
              </div>
            ) : route.view === "about" ? (
              <About />
            ) : route.view === "workspace" ? (
              <WorkspacePage
                key={`${user}-${route.workspaceId}`}
                token={token}
                workspaceId={route.workspaceId}
                writer={writer}
                onOpen={openSaved}
                version={saved.version}
              />
            ) : route.view === "report" ? (
              <ReportView
                key={`${user}-${route.itemId}`}
                token={token}
                itemId={route.itemId}
                writer={writer}
                label={(id) => companyName(id) ?? id}
                readable={financialsFor}
                onOpenChart={(s) => {
                  setChart(() => s);
                  saved.close("chart");
                  go({ view: "chart" });
                }}
              />
            ) : (
              <Compare
                key={user}
                token={token}
                compare={route.compare ?? {}}
                onChart={chartMany}
                saveBar={saveBar({ kind: "comps", compare: route.compare ?? {} })}
              />
            )}
            </Suspense>
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
      {help && <ShortcutSheet onClose={() => setHelp(false)} />}
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
    </ScopeContext.Provider>
  );
}

/** A palette command's tickers that resolved to nothing (in a sandbox: to none of its own). */
function noTicker(tickers: string[], sandbox: boolean): string {
  const t = tickers.join(", ");
  return sandbox
    ? `${t} ${tickers.length === 1 ? "isn't one of the companies" : "aren't among the companies"} in this demo.`
    : `No company with the ticker ${t}.`;
}

import { Suspense, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { ApiError, demoUsers, login, me as fetchMe, watchlistCsv } from "./api";
import { AssistantPanel } from "./assistant/AssistantPanel";
import { metricLabel } from "./chart/metrics";
import { companiesChart, companyChart, emptyChart, type ChartState, type Transform } from "./chart/model";
import { useChart } from "./chart/useChart";
import { directoryFor, DirectoryContext, UNNAMED } from "./companies/directory";
import { uiContext } from "./assistant/actions";
import { pageContext } from "./assistant/context";
import { useAssistant } from "./assistant/useAssistant";
import { useUiActions } from "./assistant/useUiActions";
import { ErrorBoundary } from "./components/ErrorBoundary";
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
  AnalysisPage,
  ChartBuilder,
  Companies,
  Company,
  Compare,
  FilingReader,
  prefetchScreens,
  ReportView,
  Search,
  ModelPage,
  Settings,
  Status,
  ThesisPage,
  WorkspacePage,
} from "./pages/lazy";
import { readableCount, Welcome } from "./pages/Welcome";
import { go, href, parse, useRoute } from "./route";
import { resolveTicker } from "./palette/commands";
import { useShortcuts } from "./shortcuts";
import { inScope, ScopeContext, scopeOf } from "./scope";
import { accountName, catalogOpen, readableFor, readableTotal } from "./access";
import {
  clearFragment,
  clearSession,
  loadSession,
  pickerEnabled,
  readSignedIn,
  resetsIn,
  signInError,
  type Session,
} from "./session";
import { load, save } from "./storage";
import { useReportWriter } from "./reports/useReportWriter";
import { useTheme } from "./theme";
import { screenTitle } from "./title";
import { ColumnPicker, HomeLayoutEditor } from "./views/Editors";
import { columnSpecs, DEFAULT_SCREENER_COLUMNS, DEFAULT_WATCHLIST_COLUMNS } from "./views/model";
import { useViews } from "./views/useViews";
import { useMarketData } from "./market/useMarketData";
import type { Me } from "./types";
import { itemRoute, restoreChartState, type Current } from "./workspaces/model";
import { Recent } from "./workspaces/Recent";
import { SaveBar } from "./workspaces/SaveBar";
import { useSaved } from "./workspaces/useSaved";
import { WorkspaceTree } from "./workspaces/WorkspaceTree";

const DRAWER_KEY = "tributary.assistant";
const DRAWER_FITS_PX = 1200;

const SANDBOX_EXPIRED = "Your demo sandbox expired and was reset. Start a new one.";
const GITHUB_EXPIRED = "Your sign-in expired. Sign in with GitHub again.";
const ACCOUNT_EXPIRED = "Your sign-in link expired. Ask for a new one.";
/** an owner-created username/password account ("pw-<username>"): it can just sign in again */
const PASSWORD_EXPIRED = "Your sign-in expired. Sign in again.";
const expired = (s: Session) =>
  s.kind === "github"
    ? GITHUB_EXPIRED
    : s.kind === "account"
      ? s.userId.startsWith("pw-")
        ? PASSWORD_EXPIRED
        : ACCOUNT_EXPIRED
      : SANDBOX_EXPIRED;

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
  const [frontDoor, setFrontDoor] = useState<{
    notice: string | null;
    /** back from GitHub with a token: Welcome finishes signing in */
    github?: { token: string; expiresIn: number };
  } | null>(null);
  const [clock, setClock] = useState(() => Date.now());
  // a demo sandbox lists only its own companies everywhere (scope.ts) while the public catalog
  // is closed; with it open every company is readable, so nothing is scoped
  const open = !!me?.catalog?.public;
  const scope = useMemo(() => (open ? null : scopeOf(session)), [session, open]);
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
  const workspace = useWorkspace(token, user, me?.companies.map((c) => c.company_id), watchCols, !!me && !me.catalog?.public);
  const saved = useSaved(token, user);
  // "Connect your data": offered only when the server has it on (useMarketData's docstring)
  const market = useMarketData(token);
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
  const [replacedChart, setReplacedChart] = useState<ChartState | null>(null);
  useEffect(() => setReplacedChart(null), [user]); // never another user's draft
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
      .catch(() => setError("Can't reach the server. Try again in a moment."));
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

  // back from "Sign in with GitHub" (#/signed-in?token=… or ?error=…), else resume a sandbox or
  // GitHub session from an earlier visit, else the front door
  useEffect(() => {
    if (picker) return;
    const back = readSignedIn(window.location.hash);
    if (back) {
      clearFragment(); // the token leaves the address bar and history at once
      if ("error" in back) {
        setFrontDoor({ notice: signInError(back.error) });
      } else {
        clearSession();
        setFrontDoor({ notice: null, github: back });
      }
      return;
    }
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
        if (e instanceof ApiError && e.status === 401) signOut(expired(stored));
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
    if (session && Date.parse(session.expiresAt) <= clock) signOut(expired(session));
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
          if (item.kind === "report" || item.kind === "thesis" || item.kind === "model") {
            return go({ view: item.kind, itemId: item.item_id });
          }
          if (item.kind === "chart") setChart(() => restoreChartState(item.state));
          go(itemRoute(item.kind, item.state));
        })
        .catch(() => setError("Couldn't open that item. It may have been deleted; the list is refreshed."));
    },
    [saved, setChart],
  );

  // a thesis or model of an analysis project opens inside the project (its header and steps)
  const inProject = useMemo(() => {
    if ((route.view !== "thesis" && route.view !== "model") || saved.list.state !== "ok") return null;
    const id = route.itemId;
    const ws = saved.list.workspaces.find((w) => w.project && w.items.some((i) => i.item_id === id));
    return ws ? href({ view: "analysis", projectId: ws.workspace_id, step: route.view, itemId: id }) : null;
  }, [route, saved.list]);
  useEffect(() => {
    if (inProject) window.location.replace(inProject);
  }, [inProject]);

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

  // names for anything that holds only an id (companies/directory.ts): with the catalog open
  // `me.companies` has only private grants, so most companies are known only by asking
  const directory = useMemo(() => directoryFor(token), [token]);
  const namesVersion = useSyncExternalStore(directory.subscribe, directory.snapshot);
  useEffect(() => {
    directory.learn([
      ...(me?.companies ?? []).flatMap((c) => (c.name ? [{ companyId: c.company_id, name: c.name, ticker: null }] : [])),
      ...workspace.rows.map((r) => ({ companyId: r.companyId, name: r.name, ticker: r.ticker })),
    ]);
  }, [directory, me, workspace.rows]);
  const watched = useMemo(
    () =>
      workspace.watchlist.state === "ok"
        ? new Set(workspace.watchlist.data.companies.map((c) => c.company_id))
        : null,
    [workspace.watchlist],
  );
  const watchedIds = useMemo(() => workspace.rows.map((r) => r.companyId), [workspace.rows]);
  const searchSuggestions = useMemo(
    () => workspace.rows.map((r) => ({ companyId: r.companyId, name: r.name, ticker: r.ticker })),
    [workspace.rows],
  );
  // new identities whenever a name arrives, so what's built from them follows
  const companyName = useCallback((id: string) => directory.name(id), [directory, namesVersion]);
  const companyLabel = useCallback((id: string) => directory.label(id), [directory, namesVersion]);
  const companyShort = useCallback((id: string) => directory.short(id), [directory, namesVersion]);
  const filingsFor = useMemo(() => readableFor(me, "filings"), [me]);
  const financialsFor = useMemo(() => readableFor(me, "financials"), [me]);
  watchedRef.current = watched;
  nameRef.current = companyName;
  // what the reader has open, in words (it knows the form and date once the filing has loaded)
  const [readerFiling, setReaderFiling] = useState<{ accessionNo: string; label: string } | null>(null);
  const filingText = route.view === "filing" && readerFiling?.accessionNo === route.accessionNo ? readerFiling.label : null;
  const chartCompany = chart.layers.find((l) => l.companyId ?? l.defaultCompany);
  const researchCompany = useMemo(() => {
    if (route.view === "chart") return chartCompany?.companyId ?? chartCompany?.defaultCompany ?? null;
    if (route.view !== "analysis" || saved.list.state !== "ok") return null;
    return saved.list.workspaces.find((w) => w.workspace_id === route.projectId)?.project?.companyId ?? null;
  }, [route, chartCompany, saved.list]);
  const context = useMemo(
    () => pageContext(route, companyName, (id) => inScope(scope, id), {
      watchlist: watchedIds,
      company: researchCompany,
      charted: chart.layers.length > 0,
      filing: filingText,
      title: companyLabel,
    }),
    [route, companyName, companyLabel, scope, watchedIds, researchCompany, chart.layers.length, filingText],
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
          name: c.name ?? UNNAMED,
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

  // "New chart" empties the builder (and detaches it from a saved chart, so Save can't overwrite
  // it), keeping the draft for one Restore; "Chart builder" in the nav still reopens the draft
  const newChart = () => {
    const before = chartRef.current;
    if (before.layers.length || before.title) setReplacedChart(before);
    setChart(() => emptyChart());
    saved.close("chart");
    go({ view: "chart" });
  };
  const restoreChartDraft = () => {
    if (replacedChart) setChart(() => replacedChart);
    setReplacedChart(null);
  };

  const saveBar = (current: Current) =>
    saved.list.state === "unavailable" ? null : (
      <SaveBar current={current} saved={saved} label={(id) => companyName(id) ?? UNNAMED} />
    );

  // the browser tab says what's on screen
  const savedName = useCallback((id: string) => {
    if (saved.list.state !== "ok") return null;
    for (const w of saved.list.workspaces) {
      if (w.workspace_id === id) return w.name;
      const item = w.items.find((i) => i.item_id === id);
      if (item) return item.name;
    }
    return null;
  }, [saved.list]);
  useEffect(() => {
    document.title = !picker && !token
      ? route.view === "status" ? screenTitle(route, { company: () => null }) : "Tributary"
      : screenTitle(route, { company: companyLabel, filing: filingText, chart: chart.title, saved: savedName });
  }, [picker, token, route, companyLabel, filingText, chart.title, savedName]);

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

  // the status page is public: signed out, it shows without the app shell
  if (!picker && !token && frontDoor && route.view === "status") {
    return (
      <main className="welcome status-public">
        <Suspense fallback={<p className="muted">Loading the status…</p>}>
          <Status signedOut />
        </Suspense>
      </main>
    );
  }

  if (!picker && !token && frontDoor) {
    return (
      <Welcome
        notice={frontDoor.notice}
        github={frontDoor.github}
        onReady={(s, m) => {
          signIn(s, m);
          go({ view: "home" });
        }}
      />
    );
  }

  return (
    <ScopeContext.Provider value={scope}>
      <DirectoryContext.Provider value={directory}>
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
          connections={market?.enabled ?? false}
          sandboxResetsIn={session?.kind === "sandbox" ? resetsIn(session.expiresAt, clock) : null}
          workspaces={token ? <WorkspaceTree saved={saved} onOpen={openSaved} onError={setError} /> : null}
        />
        {navOpen && <div className="scrim" aria-hidden="true" onClick={() => setNavOpen(false)} />}
        <div className="main">
          <TopBar
            menuOpen={navOpen}
            onMenu={() => setNavOpen((o) => !o)}
            route={route}
            companyName={companyLabel}
            filingLabel={filingText}
            users={users}
            user={session?.kind === "sandbox" ? "demo sandbox" : user}
            account={(session?.kind === "github" || session?.kind === "account") && session.login
              ? { login: me ? accountName(me) : session.login, onSignOut: () => signOut(null) }
              : undefined}
            busy={assistant.busy}
            theme={theme}
            assistantOpen={showDrawer}
            onUser={setUser}
            onSearch={() => setPalette("")}
            onTheme={toggleTheme}
            onAssistant={() => (drawerOpen ? setDrawer(false) : focusAssistant())}
          />
          {session?.kind === "sandbox" && (
            <div className="sandbox-banner" role="note">
              Demo sandbox: {open ? "every company" : `${session.companies.length} companies`} · resets in{" "}
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
            <ErrorBoundary resetKey={window.location.hash}>
            <Suspense fallback={<div className="page"><Skeleton lines={6} label="Loading the page" /></div>}>
            {route.view === "status" ? (
              <Status />
            ) : !token ? (
              <p className="page muted">Signing in…</p>
            ) : route.view === "home" ? (
              <Home
                workspace={workspace}
                lastIngested={ingested}
                onAdd={() => setPalette(WATCH_PREFIX)}
                onExport={exportWatchlist}
                readableCompanies={me ? readableTotal(me) : 0}
                onNewChart={newChart}
                recent={saved.list.state === "unavailable" ? null : <Recent recent={saved.recent} onOpen={openSaved} />}
                notes={<RecentNotes token={token} name={companyLabel} filingsFor={filingsFor} catalogOpen={catalogOpen(me, "financials")} />}
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
                statements={route.statements}
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
                onOpenChart={(s) => {
                  setChart(() => s);
                  saved.close("chart");
                  go({ view: "chart" });
                }}
                onAsk={(q) => {
                  assistant.setDraft(q);
                  focusAssistant();
                }}
                saved={saved.list.state === "ok" ? saved : null}
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
                onFiling={(label) => setReaderFiling({ accessionNo: route.accessionNo, label })}
              />
            ) : route.view === "search" ? (
              <Search key={user} token={token} search={route.search} companies={me?.companies ?? []}
                open={catalogOpen(me, "filings")} suggestions={searchSuggestions} />
            ) : route.view === "chart" ? (
              <ChartBuilder
                key={user}
                token={token}
                chart={chart}
                setChart={setChart}
                saveBar={saveBar({ kind: "chart", chart })}
                readable={financialsFor}
                preferred={watchedIds}
                onNew={newChart}
                onRestore={replacedChart ? restoreChartDraft : null}
              />
            ) : route.view === "ask" ? (
              <div className="page ask-page">
                <div className="row">
                  <h1 className="grow">Ask</h1>
                </div>
                <AssistantPanel variant="page" {...panelProps} />
                <p className="faint small">
                  The same assistant as the side panel, full width. It answers only from data{" "}
                  {session?.kind === "sandbox" ? "your sandbox" : session ? "your account" : (user ?? "you")} can read,
                  with the filing behind every
                  number.
                </p>
              </div>
            ) : route.view === "about" ? (
              <About />
            ) : route.view === "settings" ? (
              <Settings key={user} token={token} />
            ) : route.view === "workspace" ? (
              <WorkspacePage
                key={`${user}-${route.workspaceId}`}
                token={token}
                workspaceId={route.workspaceId}
                writer={writer}
                onOpen={openSaved}
                version={saved.version}
                saved={saved.list.state === "ok" ? saved : null}
              />
            ) : (route.view === "thesis" || route.view === "model") && inProject ? (
              <div className="page"><Skeleton lines={6} label="Opening the analysis" /></div>
            ) : route.view === "analysis" ? (
              <AnalysisPage
                key={user}
                token={token}
                user={user}
                projectId={route.projectId}
                step={route.step}
                itemId={route.itemId}
                saved={saved}
                readable={financialsFor}
                label={companyShort}
                onOpenChart={(s) => {
                  setChart(() => s);
                  saved.close("chart");
                  go({ view: "chart" });
                }}
                onChartMany={chartMany}
              />
            ) : route.view === "thesis" ? (
              <ThesisPage
                key={`${user}-${route.itemId}`}
                token={token}
                user={user}
                itemId={route.itemId}
                saved={saved}
                label={companyShort}
                readable={financialsFor}
                onOpenChart={(s) => {
                  setChart(() => s);
                  saved.close("chart");
                  go({ view: "chart" });
                }}
              />
            ) : route.view === "model" ? (
              <ModelPage key={`${user}-${route.itemId}`} token={token} itemId={route.itemId} saved={saved}
                label={companyLabel} />
            ) : route.view === "report" ? (
              <ReportView
                key={`${user}-${route.itemId}`}
                token={token}
                itemId={route.itemId}
                writer={writer}
                label={companyShort}
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
            </ErrorBoundary>
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
          watched={watched}
          actions={actions}
          catalogOpen={catalogOpen(me, "financials")}
          onClose={() => setPalette(null)}
        />
      )}
      </DirectoryContext.Provider>
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

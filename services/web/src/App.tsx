import { Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { ApiError, apiKeys, connectedApps, demoUsers, login, me as fetchMe, watchlistCsv } from "./api";
import { useAlertsUnread } from "./alerts/useAlerts";
import { AssistantPanel } from "./assistant/AssistantPanel";
import { metricLabel } from "./chart/metrics";
import { chartTitle, companiesChart, companyChart, emptyChart, MAX_COMPANY_LINES, type ChartState, type Transform } from "./chart/model";
import { useChart } from "./chart/useChart";
import { directoryFor, DirectoryContext, UNNAMED } from "./companies/directory";
import { uiContext } from "./assistant/actions";
import { pageContext } from "./assistant/context";
import { AllowanceContext, useAllowance } from "./assistant/useAllowance";
import { useAssistant } from "./assistant/useAssistant";
import { useUiActions } from "./assistant/useUiActions";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { NotInDemo } from "./components/NotInDemo";
import { Palette, WATCH_PREFIX, type PaletteActions, type PaletteCompany, type PalettePlace } from "./components/Palette";
import { ShortcutSheet } from "./components/ShortcutSheet";
import { Sidebar } from "./components/Sidebar";
import { Skeleton } from "./components/States";
import { TopBar } from "./components/TopBar";
import { download } from "./download";
import { lastIngested, watchColumns } from "./home/data";
import { RecentNotes } from "./home/RecentNotes";
import { StartCard } from "./home/StartCard";
import { useFeedStatus } from "./home/useFeedStatus";
import { useWorkspace } from "./home/useWorkspace";
import { Home } from "./pages/Home";
import {
  About,
  AlertsPage,
  AnalysisPage,
  ApiDocs,
  ChartBuilder,
  Companies,
  Company,
  Compare,
  Connect,
  FilingReader,
  prefetchScreens,
  ReportView,
  Search,
  ModelPage,
  PortfolioPage,
  DataPage,
  Settings,
  Status,
  ThesisPage,
  WorkspacePage,
} from "./pages/lazy";
import { readableCount, Welcome } from "./pages/Welcome";
import { loadPending, savePending } from "./connect/connect";
import { go, href, pageOf, parse, useRoute, type Route } from "./route";
import { resolveTicker } from "./palette/commands";
import { useShortcuts } from "./shortcuts";
import { markKeyboard, T, viaKeyboard } from "./motion";
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
import { currentFingerprint, itemRoute, restoreChartState, type Current } from "./workspaces/model";
import { Recent } from "./workspaces/Recent";
import { SaveBar } from "./workspaces/SaveBar";
import { useSaved } from "./workspaces/useSaved";
import { WorkspaceTree } from "./workspaces/WorkspaceTree";
import { useWatches } from "./watches/useWatches";
import { SavedScreens, WatchControl, WatchHint } from "./watches/WatchControl";
import { ConfirmDelete, UndoDeletes } from "./workspaces/Actions";

/** What ending a demo loses, asked first (Riley, test 10, finding 17: one click lost it all). */
const END_DEMO_DETAIL = "You leave this sandbox and can't come back to it: the holdings, journal, analyses, models, theses, " +
  "metrics and watched screens you made in it are lost (it is deleted when it resets). A new demo starts empty.";
import { toast } from "./components/Toasts";

const DRAWER_KEY = "tributary.assistant";

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

/** Skip to content: the focus on the page's h1 (no ring: a heading given the focus shows none), else on
 * the page itself, so the next Tab is the page's first control. */
function skipToContent(): void {
  const main = document.querySelector<HTMLElement>("main.content");
  const to = main?.querySelector<HTMLElement>("h1") ?? main;
  if (!to) return;
  if (!to.hasAttribute("tabindex")) to.setAttribute("tabindex", "-1");
  to.focus();
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
  // closed until asked for: a first visit sees the page at full width, and the top bar's Assistant
  // is one click (or Ctrl J) away. The choice is remembered per browser.
  const [drawerOpen, setDrawerOpen] = useState(() => load(DRAWER_KEY) === "open");
  const [palette, setPalette] = useState<string | null>(null);
  const [help, setHelp] = useState(false);
  const [focusTick, setFocusTick] = useState(0);
  const input = useRef<HTMLTextAreaElement>(null);
  const route = useRoute();
  // a request to connect an app waits in this tab while the visitor signs in (connect/connect.ts)
  useEffect(() => {
    if (!token && route.view === "connect" && route.request) savePending(route.request);
  }, [token, route]);
  // narrow screens: the sidebar is a drawer behind the Menu button (styles.css), closed on
  // every navigation and on Escape
  const [navOpen, setNavOpen] = useState(false);
  useEffect(() => setNavOpen(false), [route]);
  // a new page starts at its top (route.ts pageOf); a layout effect, so a page's own scroll to a passage
  // (the reader's ?at=, its passive effect) still wins
  const page = pageOf(route);
  const firstPage = useRef(true);
  useLayoutEffect(() => {
    if (firstPage.current) {
      firstPage.current = false;
      return;
    }
    window.scrollTo(0, 0);
  }, [page]);
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
  const [theme, toggleTheme, setTheme] = useTheme(user);
  const views = useViews(token, user);
  const [customizing, setCustomizing] = useState(false);
  const watchCols = useMemo(() => watchColumns(views.watchlistColumns, views.options), [views.watchlistColumns, views.options]);
  const screenCols = useMemo(() => columnSpecs(views.screenerColumns, views.options), [views.screenerColumns, views.options]);
  const workspace = useWorkspace(token, user, me?.companies.map((c) => c.company_id), watchCols, !!me && !me.catalog?.public);
  const saved = useSaved(token, user);
  // Riley 21: a saved screen stayed bound to whatever screen came next, so Save would overwrite it with
  // another. Arriving at the screener from another page with a different screen than the one open is a new
  // screen, not an edit of it; changing filters on the page still is.
  const lastView = useRef(route.view);
  useEffect(() => {
    const arrived = route.view === "companies" && lastView.current !== "companies";
    lastView.current = route.view;
    const open = saved.open.screen;
    if (arrived && open && currentFingerprint({ kind: "screen", screen: route.screen ?? {} }) !== open.saved) saved.close("screen");
  }, [route]);
  // "Connect your data": offered only when the server has it on (useMarketData's docstring)
  const market = useMarketData(token);
  // F6: the bell's count (null: no alerts on this server, so no bell)
  const alertCount = useAlertsUnread(token);
  // saved screens that alert (null: an older API, nothing offered)
  const watching = useWatches(token);
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
  const [replacedChart, setReplacedChart] = useState<{ state: ChartState; why: string } | null>(null);
  const [chartNotice, setChartNotice] = useState<string | null>(null);
  useEffect(() => {
    setReplacedChart(null); // never another user's draft
    setChartNotice(null);
  }, [user]);
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

  const allowanceState = useAllowance(token, assistant.busy);
  const { allowance, refresh: refreshAllowance } = allowanceState;
  // a report ran: it counted, several times over
  useEffect(() => {
    if (writer.run.status !== "writing") refreshAllowance();
  }, [writer.run.status, refreshAllowance]);

  useEffect(() => {
    if (!picker) return;
    demoUsers()
      .then((u) => {
        setUsers(u);
        setUser(u[0] ?? null);
      })
      // only a failure that clears by itself says to try again soon (a 404: no demo users here)
      .catch((e) => setError(e instanceof ApiError && e.status < 500
        ? "Signing in as a demo user isn't available on this server."
        : "Can't reach the server. Try again in a moment."));
  }, [picker]);

  const signIn = useCallback((s: Session, m: Me | null) => {
    setSession(s);
    setUser(s.userId);
    setToken(s.token);
    setMe(m);
    setFrontDoor(null);
    setClock(Date.now());
  }, []);

  // End demo asks first: the sandbox can't be come back to (finding 17)
  const [endingDemo, setEndingDemo] = useState(false);
  const signOut = useCallback((notice: string | null) => {
    // the front door at the site's own address, not the page that was open (Riley 93: #/search?q=tariffs)
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
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

  // the panel slides in over the room the page gave up at once, and slides away to close (§7.4); Ctrl J
  // or Escape is instant. Closing, it stays mounted for its 180ms, and focus goes back to its opener.
  const [drawerLeaving, setDrawerLeaving] = useState(false);
  const drawerInstant = useRef(false);
  const drawerWasOpen = useRef(drawerOpen);
  const setDrawer = useCallback((open: boolean) => {
    const instant = viaKeyboard();
    drawerInstant.current = instant;
    if (!open && drawerWasOpen.current) {
      if (document.activeElement?.closest(".ai-drawer")) {
        window.requestAnimationFrame(() => document.querySelector<HTMLElement>(".assistant-btn")?.focus({ preventScroll: true }));
      }
      if (!instant) {
        setDrawerLeaving(true);
        window.setTimeout(() => setDrawerLeaving(false), T.panelOut);
      }
    }
    if (open) setDrawerLeaving(false);
    drawerWasOpen.current = open;
    setDrawerOpen(open);
    save(DRAWER_KEY, open ? "open" : "closed");
  }, []);

  /** Open the drawer (except on Ask, which is the assistant) and put the cursor in it. */
  const focusAssistant = useCallback(() => {
    if (route.view !== "ask") setDrawer(true);
    setFocusTick((n) => n + 1);
  }, [route.view, setDrawer]);

  // a question a page wrote for the user ("… in this 10-Q") means nothing on another page: it goes
  // when they leave, unless they've made it their own by editing it
  const prefilled = useRef<string | null>(null);
  const askAbout = useCallback((q: string) => {
    prefilled.current = q;
    assistant.setDraft(q);
    focusAssistant();
  }, [assistant.setDraft, focusAssistant]);
  useEffect(() => {
    if (prefilled.current !== null && assistant.draft === prefilled.current) assistant.setDraft("");
    prefilled.current = null;
  }, [route]);

  /** Anything that replaces the chart keeps the draft for one Restore, when there's work in it. */
  const keepDraft = useCallback((why: string) => {
    const before = chartRef.current;
    if (before.layers.length || before.title) setReplacedChart({ state: before, why });
    setChartNotice(null);
  }, []);
  const OPENED = "Opened a new chart.";

  /** Compare's "Chart these" and the peers tab: one line per company for a metric. */
  const chartMany = useCallback(
    (ids: string[], metric: string, transform: Transform) => {
      keepDraft(OPENED);
      setChart((s) => companiesChart(s, ids, metric, transform));
      if (ids.length > MAX_COMPANY_LINES) {
        setChartNotice(`Showing the first ${MAX_COMPANY_LINES} of ${ids.length} companies: a chart tells ${MAX_COMPANY_LINES} colours apart.`);
      }
      saved.close("chart"); // a new chart, not an edit of the saved one
      go({ view: "chart" });
    },
    [setChart, saved, keepDraft],
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
          // an own metric (B2d) lives on Your data, with the user's datasets
          if (item.kind === "metric") return go({ view: "data" });
          if (item.kind === "chart") {
            keepDraft(`Opened ${item.name}.`);
            setChart(() => restoreChartState(item.state));
          }
          go(itemRoute(item.kind, item.state));
        })
        .catch(() => setError("Couldn't open that item. It may have been deleted; the list is refreshed."));
    },
    [saved, setChart, keepDraft],
  );

  // an alert's link to a saved screen (#/screen/<id>): opened as Companies' open screen, in place of
  // the link (Back goes where the user came from, not to the link again)
  useEffect(() => {
    if (route.view !== "screen" || !token) return;
    let live = true;
    saved
      .openItem(route.itemId)
      .then((item) => live && window.location.replace(href(item.kind === "screen" ? itemRoute("screen", item.state) : { view: "home" })))
      .catch(() => {
        if (!live) return;
        setError("Couldn't open that saved screen. It may have been deleted.");
        window.location.replace(href({ view: "companies" }));
      });
    return () => {
      live = false;
    };
    // the link's item only
  }, [route.view === "screen" ? route.itemId : null, token]);
  // the user's saved screens, listed on Companies
  const savedScreens = useMemo(() => saved.list.state === "ok"
    ? saved.list.workspaces.flatMap((w) => w.items.filter((i) => i.kind === "screen").map((i) => ({ item_id: i.item_id, name: i.name })))
    : [], [saved.list]);

  // the user's own metrics (B2d, Your data): offered as screener columns
  const ownMetrics = useMemo(() => saved.list.state === "ok"
    ? saved.list.workspaces.flatMap((w) => w.items.filter((i) => i.kind === "metric").map((i) => ({ item_id: i.item_id, name: i.name })))
    : null, [saved.list]);

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
      .then((blob) => {
        download("watchlist.csv", blob);
        toast({ text: "Exported your watchlist to watchlist.csv.", topic: "watchlist-csv" });
      })
      .catch(() => setError("Couldn't export the watchlist. Try again."));
  }, [token, views.watchlistColumns]);

  useShortcuts((s) => {
    // a shortcut's change is instant (spec §7.2): the palette, the panel and a page change by key
    markKeyboard();
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
  // Home's data notes with the catalog open: what this user chose, not every company (null: still loading)
  const ownCompanies = useMemo(() => {
    if (workspace.watchlist.state === "loading") return null;
    const projects = saved.list.state === "ok" ? saved.list.workspaces.flatMap((w) => (w.project ? [w.project.companyId] : [])) : [];
    return [...new Set([...watchedIds, ...projects])];
  }, [workspace.watchlist.state, watchedIds, saved.list]);
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
      charted: chart.layers.flatMap((l) => l.companyId ?? l.defaultCompany ?? []),
      chartHasLayers: chart.layers.length > 0,
      filing: filingText,
      title: companyLabel,
    }),
    [route, companyName, companyLabel, scope, watchedIds, researchCompany, chart.layers, filingText],
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
      // an add says so (Riley 69: "+ Add" gave no confirmation)
      watch: (companyId) => {
        workspace.watch(companyId)
          .then(() => toast({ text: `Added ${companyName(companyId) ?? "it"} to your watchlist.`, topic: `watch-${companyId}`,
            action: { label: "Open it", onClick: () => go({ view: "company", companyId }) } }))
          .catch(() => setError("Couldn't add that company. Try again."));
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
            chartMany([found.companyId], metric, "ttm");
          })
          .catch(() => setError("Couldn't look that ticker up. Try again."));
      },
    }),
    [workspace, focusAssistant, assistant, toggleTheme, exportWatchlist, token, chartMany, scope],
  );

  // the account's API keys and connected apps, as the palette offers them: asked once the palette first
  // opens, and only offered where the API allows them (it decides who may hold keys, never the session kind)
  const [account, setAccount] = useState<{ token: string; keys: boolean; apps: boolean } | null>(null);
  useEffect(() => {
    if (palette === null || !token || account?.token === token) return;
    let live = true;
    void Promise.all([
      apiKeys(token).then((k) => !!k?.allowed, () => false),
      connectedApps(token).then((c) => !!c?.allowed, () => false),
    ]).then(([keys, apps]) => live && setAccount({ token, keys, apps }));
    return () => {
      live = false;
    };
  }, [palette, token, account]);
  // pages beyond the nav's screens, saved screens and the account's sections, found by what they do
  // (Riley, test 10, finding 37: "alert", "screen", "watch" and "api" found none of them)
  const places = useMemo((): PalettePlace[] => {
    const at = (r: Route) => () => go(r);
    const settingsAt = (id: string) => () => {
      go({ view: "settings" });
      scrollWhenShown(id);
    };
    const screens = saved.list.state === "ok"
      ? saved.list.workspaces.flatMap((w) => w.items.filter((i) => i.kind === "screen"))
      : [];
    return [
      { key: "alerts", label: "Alerts", sub: "page: new filings and screen alerts", words: "alert notifications bell", run: at({ view: "alerts" }) },
      { key: "settings", label: "Settings", sub: "page: filing alerts, webhooks", words: "alerts webhook webhooks preferences email", run: at({ view: "settings" }) },
      { key: "api", label: "Using the API", sub: "page: keys, spreadsheets, your AI model", words: "api keys excel google sheets spreadsheet connect ai claude chatgpt mcp script developer", run: at({ view: "api" }) },
      ...(account?.token === token && account.keys ? [{ key: "keys", label: "API keys", sub: "Settings", words: "api key token", run: settingsAt("api-keys") }] : []),
      ...(account?.token === token && account.apps ? [{ key: "apps", label: "Connected apps", sub: "Settings", words: "connect ai claude chatgpt mcp oauth", run: settingsAt("connected-apps") }] : []),
      { key: "status", label: "Data status", sub: "page: freshness and checks", words: "freshness quality checks", run: at({ view: "status" }) },
      { key: "about", label: "What Tributary covers", sub: "page", words: "about coverage prices", run: at({ view: "about" }) },
      { key: "search", label: "Search filings", sub: "page: full text", words: "full text", run: at({ view: "search" }) },
      ...(watching.list ? [{ key: "watch", label: "Watch a screen", sub: "Companies: save a screen, then Watch", words: "alerts screener watched", run: at({ view: "companies" }) }] : []),
      ...screens.map((i) => ({
        key: `screen-${i.item_id}`, label: `Screen: ${i.name}`, sub: watching.of(i.item_id) ? "saved screen, watched" : "saved screen",
        words: "screens saved watch", run: at({ view: "screen", itemId: i.item_id }),
      })),
    ];
  }, [saved.list, watching, account, token]);

  // "New chart" empties the builder (and detaches it from a saved chart, so Save can't overwrite
  // it), keeping the draft for one Restore; "Chart builder" in the nav still reopens the draft
  const newChart = () => {
    keepDraft("Started a new chart.");
    setChart(() => emptyChart());
    saved.close("chart");
    go({ view: "chart" });
  };
  const restoreChartDraft = () => {
    if (replacedChart) setChart(() => replacedChart.state);
    setReplacedChart(null);
    setChartNotice(null);
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
      ? route.view === "status" || route.view === "api" ? screenTitle(route, { company: () => null }) : "Tributary"
      : screenTitle(route, { company: companyLabel, filing: filingText, chart: chartTitle(chart, companyShort, metricLabel), saved: savedName });
  }, [picker, token, route, companyLabel, companyShort, filingText, chart, savedName]);

  const showDrawer = drawerOpen && route.view !== "ask";
  const ingested = lastIngested(workspace.latest ?? []);
  const listed = useFeedStatus(token !== null);
  const panelProps = {
    assistant,
    onUndo: (id: number) => void uiActions.undo(id),
    undone: uiActions.undone,
    context,
    signedIn: token !== null,
    inputRef: input,
    allowance,
  };

  // the status page and "Using the API" are public: signed out, they show without the app shell
  if (!picker && !token && frontDoor && (route.view === "status" || route.view === "api")) {
    return (
      <main className="welcome status-public">
        <Suspense fallback={<p className="muted">Loading…</p>}>
          {route.view === "status" ? <Status signedOut /> : <ApiDocs signedOut />}
        </Suspense>
      </main>
    );
  }

  // a problem /oauth/authorize found needs no sign-in to be read
  if (!picker && !token && frontDoor && route.view === "connect" && route.error) {
    return (
      <main className="welcome connect-page">
        <Suspense fallback={<p className="muted">Loading…</p>}>
          <Connect token="" me={null} error={route.error} />
        </Suspense>
      </main>
    );
  }

  if (!picker && !token && frontDoor) {
    const connecting = route.view === "connect" || !!loadPending();
    return (
      <>
      <Welcome
        notice={frontDoor.notice ?? (connecting ? "Sign in to Tributary to connect your app: you'll see what it asks for, and approve it, next." : null)}
        github={frontDoor.github}
        onReady={(s, m) => {
          signIn(s, m);
          // back to the app's request, if one was waiting (only ever a sealed request, never a URL)
          const pending = loadPending();
          go(pending ? { view: "connect", request: pending } : { view: "home" });
        }}
      />
      {/* the toasts (End demo's) at the front door's foot too */}
      <UndoDeletes saved={null} />
      </>
    );
  }

  // the consent screen stands alone, like the front door
  if (token && route.view === "connect") {
    return (
      <main className="welcome connect-page">
        <Suspense fallback={<p className="muted">Loading…</p>}>
          <Connect token={token} me={me} request={route.request} error={route.error} />
        </Suspense>
      </main>
    );
  }

  return (
    <ScopeContext.Provider value={scope}>
      <DirectoryContext.Provider value={directory}>
      <AllowanceContext.Provider value={allowanceState}>
      <div className={showDrawer ? "app with-ai" : "app"}>
        {/* the first Tab stop: past the top bar and the rail to the page's heading (Marek 13: a company page's
            content was the 38th stop). A button: an in-page link would change the hash route. */}
        <button type="button" className="skip-link" onClick={skipToContent}>Skip to content</button>
        <TopBar
          menuOpen={navOpen}
          onMenu={() => setNavOpen((o) => !o)}
          route={route}
          users={users}
          user={session?.kind === "sandbox" ? "demo sandbox" : user}
          account={(session?.kind === "github" || session?.kind === "account") && session.login
            ? { login: me ? accountName(me) : session.login, onSignOut: () => signOut(null) }
            : undefined}
          sandbox={session?.kind === "sandbox"
            ? { covers: open ? "every company" : `${session.companies.length} companies`, resetsIn: resetsIn(session.expiresAt, clock), onEnd: () => setEndingDemo(true) }
            : null}
          busy={assistant.busy}
          assistantOpen={showDrawer}
          onUser={setUser}
          onSearch={() => setPalette("")}
          onAssistant={() => (drawerOpen ? setDrawer(false) : focusAssistant())}
          alerts={alertCount}
          listed={listed}
          lastIngested={ingested}
          valuesAsOf={workspace.watchlist.state === "ok" ? workspace.watchlist.data.as_of : null}
          me={me}
        />
        <Sidebar
          open={navOpen}
          route={route}
          me={me}
          workspace={workspace}
          onAdd={() => setPalette(WATCH_PREFIX)}
          onExport={exportWatchlist}
          onShortcuts={() => setHelp(true)}
          connections={market?.enabled ?? false}
          workspaces={token ? <WorkspaceTree saved={saved} onOpen={openSaved} onError={setError} watched={watching.watched} /> : null}
          onSignOut={session?.kind === "sandbox" ? () => setEndingDemo(true)
            : (session?.kind === "github" || session?.kind === "account") ? () => signOut(null) : undefined}
          signOutLabel={session?.kind === "sandbox" ? "End demo" : "Sign out"}
        />
        {navOpen && <div className="scrim" aria-hidden="true" onClick={() => setNavOpen(false)} />}
        <div className="main">
          {endingDemo && session?.kind === "sandbox" && (
            <ConfirmDelete text="End the demo?" detail={END_DEMO_DETAIL} yes="End demo" onNo={() => setEndingDemo(false)}
              onYes={() => {
                setEndingDemo(false);
                signOut(null);
                // the front door says it's done (Riley, test 10, finding 107: End demo gave no word)
                toast({ text: "The demo has ended: its sandbox and what you saved in it are deleted.", topic: "end-demo" });
              }} />
          )}
          {error && (
            <div className="banner" role="alert">
              {error}
              <button className="btn sm ghost" onClick={() => setError(null)}>
                Dismiss
              </button>
            </div>
          )}
          {/* a page change said to a screen reader (motion.ts settle): "Nvidia, Overview" */}
          <div id="route-announce" className="visually-hidden" aria-live="polite" />
          {/* the page's own parts take the view transition's names (a company: its head stays on a tab change) */}
          <main className={route.view === "company" ? "content" : "content vt-content"}>
            <ErrorBoundary resetKey={window.location.hash}>
            <Suspense fallback={<div className="page" aria-busy="true"><span className="visually-hidden" role="status">Loading the page</span></div>}>
            {route.view === "status" ? (
              <Status readable={me ? readableTotal(me) : null} />
            ) : route.view === "api" ? (
              <ApiDocs />
            ) : !token ? (
              // a div: a paragraph's margin moved the page 14px when it came (Marek 12)
              <div className="page muted">Signing in…</div>
            ) : route.view === "home" ? (
              <Home
                workspace={workspace}
                lastIngested={ingested}
                listed={listed}
                onAdd={() => setPalette(WATCH_PREFIX)}
                onExport={exportWatchlist}
                readableCompanies={me ? readableTotal(me) : 0}
                onNewChart={newChart}
                start={<StartCard token={token} readable={financialsFor} workspaces={saved.list.state === "ok" ? saved.list.workspaces : null}
                  alerts={alertCount} label={companyLabel} />}
                recent={saved.list.state === "unavailable" ? null : <Recent recent={saved.recent} onOpen={openSaved} />}
                notes={<RecentNotes token={token} name={companyLabel} filingsFor={filingsFor} catalogOpen={catalogOpen(me, "financials")} companies={ownCompanies} />}
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
                onChart={(id) => {
                  keepDraft(OPENED);
                  setChart((s) => companyChart(s, id));
                  saved.close("chart");
                  go({ view: "chart" });
                }}
                saveBar={saveBar({ kind: "screen", screen: route.screen ?? {} })}
                savedScreens={<SavedScreens screens={savedScreens} watched={watching.watched}
                  openId={saved.open.screen?.itemId ?? null} onOpen={openSaved} />}
                watch={saved.open.screen && watching.list ? (columns) => (
                  <WatchControl token={token} itemId={saved.open.screen!.itemId} name={saved.open.screen!.name}
                    watching={watching} columns={columns} shown={screenCols.map((c) => c.key)}
                    dirty={currentFingerprint({ kind: "screen", screen: route.screen ?? {} }) !== saved.open.screen!.saved} />
                ) : !saved.open.screen && watching.list ? () => <WatchHint /> : undefined}
                columns={screenCols}
                ownMetrics={ownMetrics}
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
                changes={route.changes}
                asOf={route.asOf}
                watched={watched ? watched.has(route.companyId) : null}
                onWatch={(on) =>
                  (on ? workspace.watch(route.companyId) : workspace.unwatch(route.companyId)).catch(() =>
                    setError("Couldn't update the watchlist. Try again."),
                  )
                }
                onChart={() => {
                  keepDraft(OPENED);
                  setChart((s) => companyChart(s, route.companyId));
                  saved.close("chart");
                  go({ view: "chart" });
                }}
                onChartMany={chartMany}
                onOpenChart={(s) => {
                  keepDraft(OPENED);
                  setChart(() => s);
                  saved.close("chart");
                  go({ view: "chart" });
                }}
                onAsk={askAbout}
                saved={saved.list.state === "ok" ? saved : null}
                readsFilingsHint={filingsFor.has(route.companyId)}
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
                mode={route.mode}
                name={companyName(route.companyId)}
                onAsk={askAbout}
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
                restoreNote={replacedChart?.why ?? null}
                onReplace={keepDraft}
                notice={chartNotice}
              />
            ) : route.view === "ask" ? (
              <div className="page ask-page">
                <div className="row page-bar">
                  <h1 className="grow">Ask</h1>
                </div>
                <AssistantPanel variant="page" {...panelProps} />
                <p className="faint small">
                  The same assistant as the side panel, at the reader's width. It answers only from data{" "}
                  {session?.kind === "sandbox" ? "your sandbox" : session ? "your account" : (user ?? "you")} can read,
                  with the filing behind every
                  number.
                </p>
              </div>
            ) : route.view === "about" ? (
              <About allowance={allowance} open={catalogOpen(me, "financials")} />
            ) : route.view === "settings" ? (
              <Settings key={user} token={token} watches={watching.list} theme={theme} onTheme={setTheme} />
            ) : route.view === "alerts" ? (
              <AlertsPage key={user} token={token} watches={watching.list} />
            ) : route.view === "portfolio" ? (
              <PortfolioPage key={`${user}:${route.import ? "import" : "holdings"}`} token={token} holdingId={route.holdingId} importing={!!route.import} />
            ) : route.view === "data" ? (
              <DataPage key={user} token={token} saved={saved.list.state === "unavailable" ? null : saved} />
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
                start={route.start}
                saved={saved}
                readable={financialsFor}
                label={companyShort}
                onOpenChart={(s) => {
                  keepDraft(OPENED);
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
                  keepDraft(OPENED);
                  setChart(() => s);
                  saved.close("chart");
                  go({ view: "chart" });
                }}
              />
            ) : route.view === "model" ? (
              <ModelPage key={`${user}-${route.itemId}`} token={token} itemId={route.itemId} saved={saved}
                label={companyLabel} />
            ) : route.view === "screen" ? (
              <div className="page"><Skeleton lines={4} label="Opening the saved screen" /></div>
            ) : route.view === "report" ? (
              <ReportView
                key={`${user}-${route.itemId}`}
                token={token}
                itemId={route.itemId}
                writer={writer}
                label={companyShort}
                readable={financialsFor}
                onOpenChart={(s) => {
                  keepDraft(OPENED);
                  setChart(() => s);
                  saved.close("chart");
                  go({ view: "chart" });
                }}
              />
            ) : route.view === "connect" ? null /* drawn without the shell, above */ : route.view === "notfound" ? (
              <div className="page not-found">
                <h1>No page here</h1>
                <p className="empty">
                  Tributary has no page at <code>#/{route.path}</code>. Find one from the menu on the left, search with Ctrl K,
                  or go <a href={href({ view: "home" })}>Home</a>.
                </p>
              </div>
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
        {(showDrawer || (drawerLeaving && route.view !== "ask")) && (
          <AssistantPanel variant="drawer" {...panelProps} onClose={() => setDrawer(false)}
            motion={[!showDrawer && "closing", drawerInstant.current && "instant"].filter(Boolean).join(" ") || undefined} />
        )}
      </div>
      {help && <ShortcutSheet onClose={() => setHelp(false)} />}
      <UndoDeletes saved={saved} />
      {palette !== null && (
        <Palette
          key={palette}
          token={token}
          initial={palette}
          companies={paletteCompanies}
          watched={watched}
          actions={actions}
          catalogOpen={catalogOpen(me, "financials")}
          places={places}
          onClose={() => setPalette(null)}
        />
      )}
      </AllowanceContext.Provider>
      </DirectoryContext.Provider>
    </ScopeContext.Provider>
  );
}

/** Scrolls to a section once the page that holds it has drawn it (a Settings section loads its own data). */
function scrollWhenShown(id: string, tries = 40) {
  const el = document.getElementById(id);
  if (el) el.scrollIntoView({ block: "start" });
  else if (tries > 0) window.setTimeout(() => scrollWhenShown(id, tries - 1), 100);
}

/** A palette command's tickers that resolved to nothing (in a sandbox: to none of its own). */
function noTicker(tickers: string[], sandbox: boolean): string {
  const t = tickers.join(", ");
  return sandbox
    ? `${t} ${tickers.length === 1 ? "isn't one of the companies" : "aren't among the companies"} in this demo.`
    : `No company with the ticker ${t}.`;
}

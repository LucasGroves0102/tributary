import { Fragment, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useDirectory } from "../companies/directory";

import { failureReason, screener, SCREENER_CSV_ROWS, screenerCsv, sectors as fetchSectors } from "../api";
import { coRegistrantNote } from "../compare/peers";
import { confidenceWords, currencyFoot, DEFAULT_SPECS, MASKED_TITLE, screenRow } from "../companies/rows";
import {
  activeFilters,
  BAND_LABELS,
  BANDS,
  checkName,
  countWords,
  DEFAULT_SORT,
  describe,
  EXCHANGES,
  FILER_CATEGORIES,
  FILER_LABELS,
  GROUP_SORT,
  hasValueFilter,
  isEmpty,
  MAX_INDUSTRIES,
  MAX_METRICS,
  MAX_SECTORS,
  MONTHS,
  PAGE_SIZE,
  screenParams,
  sortable,
  type Band,
  type FilerCategory,
  type ScreenFilters,
} from "../companies/screen";
import { ANNUAL_FORMS, NOT_MEANINGFUL, NOT_REPORTED } from "../blanks";
import { metricKey } from "../data/dataset";
import { paren } from "../format";
import { FigureText } from "../components/FigureText";
import { MenuButton } from "../components/MenuButton";
import { toast } from "../components/Toasts";
import { Why } from "../components/Why";
import { ErrorPanel, LedgerShape } from "../components/States";
import { Choices } from "../components/Choices";
import { navRow, onTableKey } from "../components/tableNav";
import { download } from "../download";
import { go, href } from "../route";
import { useScope } from "../scope";
import type { ColumnInfo, Screener, Sectors } from "../types";
import type { ColumnSpec } from "../views/model";
import { TableScroll } from "../components/TableScroll";

interface Props {
  token: string;
  screen: ScreenFilters;
  /** company ids on the user's watchlist (null while it loads or is unavailable) */
  watched: Set<string> | null;
  onWatch: (companyId: string, watch: boolean) => Promise<void>;
  onChart?: (companyId: string) => void;
  /** Save / Save as (workspaces/SaveBar) */
  saveBar?: ReactNode;
  /** the value columns, as the user chose them (views); default: SCREEN_COLUMNS */
  columns?: ColumnSpec[];
  /** "Columns" (views/Editors ColumnPicker) */
  columnPicker?: ReactNode;
  /** the user's own metrics (B2d: saved formulas, Your data), offered as columns; null: none */
  ownMetrics?: { item_id: string; name: string }[] | null;
  /** the user's saved screens (watches/SavedScreens) */
  savedScreens?: ReactNode;
  /** the open saved screen's watch (watches/WatchControl), given the columns as the table shows them */
  watch?: (columns: ColumnSpec[]) => ReactNode;
}

/** An own metric's column as the table shows it, from the API's column info (`own`). */
export function ownColumn(info: ColumnInfo): ColumnSpec {
  const own = info.own!;
  const kind: ColumnSpec["kind"] =
    info.unit === "USD" ? "usd"
      : info.unit === "USD/shares" ? "eps"
        : info.unit === "shares" ? "shares"
          : info.unit === "ratio" ? (own.display === "multiple" ? "x" : own.display === "number" ? "num" : "pct")
            : "num";
  return { key: info.key, label: own.name, long: `${own.name} (your metric: ${own.formula})`, period: info.span === "ttm" ? "TTM" : "quarter", kind };
}

type State =
  | { state: "loading" }
  /** `updating`: these are the last screen's rows while the new one runs (Riley 44: they stayed under
   * the new filter for seconds, unmarked) */
  | { state: "ok"; data: Screener; updating?: boolean }
  | { state: "error"; message: string };

/** Companies: the screener over /v1/screener. Filters live in the URL, so a screen can be
 * bookmarked, shared and undone with Back. In a demo sandbox (scope.ts) it screens only the
 * demo's companies (`access=financials`, the API's own filter), so "only companies I can
 * read" is always on and not offered. */
export function Companies({ token, screen: asked, watched, onWatch, onChart, saveBar, columns: chosen = DEFAULT_SPECS, columnPicker, ownMetrics = null, savedScreens, watch }: Props) {
  const scope = useScope();
  // in a sandbox the filters shown never include "mine": it is implied
  const screen = useMemo(() => (scope ? { ...asked, mine: undefined } : asked), [scope, asked]);
  const [result, setResult] = useState<State>({ state: "loading" });
  const [exporting, setExporting] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [sectorList, setSectorList] = useState<Sectors | null>(null);
  const columnKeys = chosen.map((c) => c.key).join(",");
  const params = useMemo(
    () => screenParams(scope ? { ...screen, mine: true } : screen, columnKeys.split(",")),
    [scope, screen, columnKeys],
  );
  const key = JSON.stringify(params);

  useEffect(() => {
    let live = true;
    setResult((r) => (r.state === "ok" ? { ...r, updating: true } : { state: "loading" }));
    screener(token, params)
      .then((data) => live && setResult({ state: "ok", data }))
      .catch(
        (e) =>
          live &&
          setResult({
            state: "error",
            message: failureReason(e),
          }),
      );
    return () => {
      live = false;
    };
    // params is keyed by its JSON so a new but equal screen doesn't refetch
  }, [token, key, attempt]);

  // the sector grouping (046), asked after the screen: null from an older API, and then no sector
  // filter or grouping is offered
  useEffect(() => {
    let live = true;
    fetchSectors(token)
      .then((s) => live && setSectorList(s && Array.isArray(s.sectors) ? s : null))
      .catch(() => live && setSectorList(null));
    return () => {
      live = false;
    };
  }, [token]);

  const set = (patch: Partial<ScreenFilters>) =>
    go({ view: "companies", screen: { ...screen, page: undefined, ...patch } });

  // a download says it happened (Riley, test 10, finding 48: Export CSV was silent), in the one toast system
  const exportCsv = () => {
    setExporting(true);
    screenerCsv(token, params)
      .then((blob) => {
        download("screener.csv", blob);
        const n = Math.min(total ?? 0, SCREENER_CSV_ROWS);
        toast({ text: `Exported ${n.toLocaleString()} ${n === 1 ? "company" : "companies"} to screener.csv.`, topic: "screener-csv" });
      })
      .catch(() => toast({ text: "Couldn't export the screen. Try again.", error: true, topic: "screener-csv" }))
      .finally(() => setExporting(false));
  };

  const data = result.state === "ok" ? result.data : null;
  // the chosen columns, then the user's own metrics the API evaluated (B2d)
  const columns = useMemo(() => [...chosen, ...(data?.columns ?? []).filter((c) => c.own).map(ownColumn)], [chosen, data]);
  const rows = useMemo(() => (data ? data.rows.map((r) => screenRow(r, columns)) : []), [data, columns]);
  // the names on screen, so a company opened from its row is named at once (its header renders before its
  // figures arrive, and its name travels from the row into the page title: motion.ts)
  const directory = useDirectory(token);
  useEffect(() => {
    directory.learn(rows.map((r) => ({ companyId: r.companyId, name: r.name, ticker: r.ticker ?? null })));
  }, [directory, rows]);
  const metricIds = screen.metric ?? [];
  const metricMenu = ownMetrics && ownMetrics.length > 0 ? (
    <MenuButton label="Your metrics" title={`Your own formulas as columns, up to ${MAX_METRICS} (Your data)`} items={ownMetrics.map((m) => ({
      label: m.name,
      checked: metricIds.includes(m.item_id),
      disabled: !metricIds.includes(m.item_id) && metricIds.length >= MAX_METRICS,
      onCheckedChange: (on: boolean) => {
        const next = on ? [...metricIds, m.item_id] : metricIds.filter((id) => id !== m.item_id);
        const sortGone = !on && (screen.sort ?? "").replace(/^-/, "") === metricKey(m.item_id);
        set({ metric: next.length ? next : undefined, ...(sortGone ? { sort: undefined } : {}) });
      },
    }))} />
  ) : null;
  const masked = rows.filter((r) => !r.readable).length;
  const total = data?.total ?? null;

  // the reading letters the page shows (§4 Readings), explained on one line under the table
  const letters = [...new Set(rows.flatMap((r) => (r.readable ? columns.map((c) => cellLetter(r.cells[c.key]!)) : [])).filter((l): l is Letter => !!l))].sort();
  const grouped = (screen.sort ?? "").replace(/^-/, "") === GROUP_SORT;

  return (
    <div className="page screener-page">
      <div className="page-bar">
        <h1>Companies</h1>
        <div className="page-actions">
          {columnPicker}
          {metricMenu}
          <button
            className="btn"
            onClick={exportCsv}
            disabled={exporting || !data || total === 0}
            title={`The whole screen, up to ${SCREENER_CSV_ROWS.toLocaleString()} rows, exact values with their period ends`}
          >
            {exporting ? "Exporting…" : "Export CSV"}
          </button>
          {saveBar}
        </div>
      </div>
      {savedScreens}
      {watch?.(columns)}

      <FilterRail screen={screen} set={set} sectors={sectorList} />

      <section className="screen-results" aria-label="Screen">
        <div className="screen-count">
          {total !== null ? (
            <span className="screen-total">
              {scope
                ? isEmpty(screen)
                  ? `${total} ${total === 1 ? "company" : "companies"} in your demo`
                  : `${total} of the ${scope.ids.size} companies in your demo`
                : `${total.toLocaleString()} ${total === 1 ? "company" : "companies"}${countWords(screen)}`}
            </span>
          ) : <span className="screen-total"><span className="blank-v w-120" /></span>}
          <span className="screen-desc" title="The filters this screen applies">{describe(screen)}</span>
          {!scope && (
            <label className="chk screen-mine" title="Only companies in my plan: those whose financials your plan covers">
              <input type="checkbox" checked={screen.mine ?? false} aria-label="Only companies in my plan"
                onChange={(e) => set({ mine: e.target.checked || undefined })} />
              Only my plan
            </label>
          )}
          {sectorList && (
            <span className="screen-group">
              <span className="screen-group-k" id="group-by">Group by</span>
              <Choices label="Group by" value={grouped ? "sector" : "none"} options={[["none", "None"], ["sector", "Sector"]]}
                set={(v) => set({ sort: v === "sector" ? GROUP_SORT : undefined })} />
            </span>
          )}
        </div>
        {result.state === "error" ? (
          <ErrorPanel what="this screen" detail={`${result.message}. Fewer columns or filters may help`}
            onRetry={() => {
              setResult({ state: "loading" });
              setAttempt((n) => n + 1);
            }} />
        ) : result.state === "loading" ? (
          <LedgerShape label="Running the screen" first="Company" columns={["Industry (SIC)", ...columns.map((c) => c.label), "FYE"]} rows={10} />
        ) : (
          <>
            {result.updating && <p className="screen-updating" role="status">Running the new screen; these are the last one's rows.</p>}
            <Table
              updating={!!result.updating}
              rows={rows}
              columns={columns}
              sort={screen.sort ?? DEFAULT_SORT}
              watched={watched}
              onSort={(sort) => set({ sort })}
              onSic={(sic) => set({ sic, industry: undefined })}
              onWatch={onWatch}
              onChart={onChart}
            />
            {rows.length === 0 && (
              <p className="empty">
                {scope ? "None of the companies in your demo match." : "No companies match."}{" "}
                {hasValueFilter(screen) && !scope
                  ? "Value filters only match figures that are current and meaningful; loosen a filter or clear it."
                  : "Loosen a filter."}
              </p>
            )}
          </>
        )}
        {data && rows.length > 0 && (
          <Pager
            offset={data.offset}
            shown={rows.length}
            total={total}
            onPage={(page) => go({ view: "companies", screen: { ...screen, page } })}
          />
        )}
        {letters.length > 0 && (
          <p className="mk-legend led-legend" aria-label="What the letters mean">
            {letters.map((l) => <span key={l}><span className="rdg-k">—<sup className="rdg">{l}</sup></span> {LETTERS[l]}</span>)}
            {rows.some((r) => r.readable && columns.some((c) => r.cells[c.key]!.text === NOT_MEANINGFUL)) && <span><span className="rdg-k">n/m</span> not meaningful</span>}
          </p>
        )}
        <p className="foot read-first">
          {currencyFoot(rows)} A negative amount is in parentheses. Hover a value for its period and source filing.
          {masked > 0 && (
            <>
              {" "}
              <span title={MASKED_TITLE}>
                {masked} {masked === 1 ? "company" : "companies"} on this page{" "}
                {masked === 1 ? "shows" : "show"} metadata only: your plan doesn't cover
                {masked === 1 ? " its" : " their"} financials.
              </span>
            </>
          )}
          {hasValueFilter(screen) && !scope &&
            " Value filters and size bands only match companies whose financials you can read."}
          {/* a once-a-year filer ranks beside the others' twelve months: said once, under the table (Riley, test 10, finding 25) */}
          {(data?.rows ?? []).some((r) => Object.values(r.sources ?? {}).some((s) => ANNUAL_FORMS.test(s?.form ?? ""))) && (
            <span className="annual-foot">
              {" "}A company that reports once a year (Form 20-F or 40-F) is ranked by its latest fiscal year, marked “annual”
              (c) beside the figure, beside the others' twelve months to their latest quarter.
            </span>
          )}
        </p>
        <details className="how-to-read">
          <summary>How to read these figures</summary>
          <div className="foot">
            TTM = trailing twelve months to each company's latest reported quarter; net debt and debt/equity at that
            quarter's end. A figure from an earlier period than the company's latest shows its date; about a year or
            more behind it reads as none since that date and leaves sorts and filters. n/m = not meaningful; a dash
            with a letter is a reading, explained on the line under the table; hover any figure for why.
          </div>
        </details>
      </section>
    </div>
  );
}

/** The reading letters a screener cell can carry (§4 Readings): words for the API's reading only. */
type Letter = "a" | "b" | "c" | "k";
const LETTERS: Record<Letter, string> = {
  a: "not available for this company (hover for why)",
  b: "none filed since a date (hover for when)",
  c: "an annual figure: a 20-F or 40-F's fiscal year",
  k: "a bank: the line doesn't describe one",
};

/** A cell's reading letter, from the API's reading (blanks.ts `readFigure`) only: never a threshold. */
export function cellLetter(cell: { value: number | null; text: string; old?: boolean; status?: string | null; asOf?: string | null }): Letter | null {
  if (cell.old) return "b";
  if (cell.status === "not_applicable") return "k";
  if (cell.status === "not_computed") return "a";
  if (cell.value === null && cell.text === NOT_REPORTED) return "a";
  if (cell.asOf?.startsWith("annual")) return "c";
  return null;
}

/** A typed filter applies this long after the last keystroke. */
const APPLY_AFTER_MS = 400;
/** A list without one name: undefined once empty, so the filter goes. */
const without = (list: string[], name: string) => {
  const left = list.filter((x) => x !== name);
  return left.length ? left : undefined;
};
/** Up to this wide the filters start folded: all nine would fill a phone's first screen. */
const FOLD_FILTERS_PX = 700;

function FilterRail({ screen, set, sectors }: {
  screen: ScreenFilters;
  set: (p: Partial<ScreenFilters>) => void;
  /** /v1/sectors (null: an older API, no sector filter) */
  sectors: Sectors | null;
}) {
  const chosenSectors = screen.sector ?? [];
  const chosenIndustries = screen.sector_industry ?? [];
  const sectorOf = (name: string) => sectors?.sectors.find((s) => s.name === name) ?? null;
  const industriesOf = (name: string) => sectorOf(name)?.industries.map((i) => i.name) ?? [];
  // the industry box offers the chosen sectors' industries; with industries alone (the assistant can set
  // them) every sector's, so more can be added
  const offered = !sectors ? []
    : chosenSectors.length ? chosenSectors.flatMap((s) => sectorOf(s) ?? [])
      : chosenIndustries.length ? sectors.sectors : [];
  // every filter applies by itself: selects at once, typed ones a moment after typing stops (or
  // on Enter or leaving the field), so nothing needs a second step
  const [q, setQ] = useState(screen.q ?? "");
  const [industry, setIndustry] = useState(screen.industry ?? "");
  const [growth, setGrowth] = useState(screen.growth?.toString() ?? "");
  const [margin, setMargin] = useState(screen.margin?.toString() ?? "");
  const [open, setOpen] = useState(() => window.innerWidth > FOLD_FILTERS_PX);
  const active = activeFilters(screen);

  const n = (s: string) => (s.trim() === "" || !Number.isFinite(Number(s)) ? undefined : Number(s));
  const drafts = () => ({
    q: q.trim() || undefined,
    industry: industry.trim() || undefined,
    growth: n(growth),
    margin: n(margin),
  });
  /** A select changed: apply it with whatever is typed, so a pending input isn't lost. */
  const apply = (patch: Partial<ScreenFilters>) => set({ ...drafts(), ...patch });
  const commit = () => {
    const next = drafts();
    if (
      next.q !== screen.q ||
      next.industry !== screen.industry ||
      next.growth !== screen.growth ||
      next.margin !== screen.margin
    ) {
      set(next);
    }
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    commit();
  };
  // the rail stays mounted while typing (a remount would drop the cursor): the address moved by
  // something else (Back, Reset filters, a shared link) sets the fields
  const fromUrl = `${screen.q ?? ""}|${screen.industry ?? ""}|${screen.growth ?? ""}|${screen.margin ?? ""}`;
  const typed = useRef(fromUrl);
  useEffect(() => {
    if (fromUrl === typed.current) return;
    typed.current = fromUrl;
    setQ(screen.q ?? "");
    setIndustry(screen.industry ?? "");
    setGrowth(screen.growth?.toString() ?? "");
    setMargin(screen.margin?.toString() ?? "");
    // only when the address changes
  }, [fromUrl]);
  const draftKey = `${q.trim()}|${industry.trim()}|${n(growth) ?? ""}|${n(margin) ?? ""}`;
  useEffect(() => {
    if (draftKey === typed.current) return;
    const t = setTimeout(() => {
      typed.current = draftKey;
      commit();
    }, APPLY_AFTER_MS);
    return () => clearTimeout(t);
    // commit reads the drafts this key describes
  }, [draftKey]);

  // the filters are one row of equal 32px controls (§2.5 chips: an applied one has an ink outline); the
  // chosen sectors and industries, a SIC code and a data check are chips above the table, each with ×
  const fchip = (on: boolean, extra = "") => `fchip${on ? " on" : ""}${extra ? ` ${extra}` : ""}`;
  const applied = chosenSectors.length + chosenIndustries.length + (screen.sic ? 1 : 0) + (screen.issue ? 1 : 0);
  return (
    <div className="filter-band">
      <button type="button" className="btn filter-toggle" aria-expanded={open} aria-controls="screen-filters"
        onClick={() => setOpen((o) => !o)}>
        Filters{active ? ` · ${active} active` : ""}
        <span className="more-caret" aria-hidden="true" />
      </button>
      <form id="screen-filters" className={open ? "filters filter-row" : "filters filter-row folded"} onSubmit={submit}
        aria-label="Screen filters">
        <span className={fchip(!!screen.q, "fchip-q")}>
          <label htmlFor="f-q" className="visually-hidden">Name or ticker</label>
          <svg className="fchip-glyph" width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
            <circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
            <path d="M10.5 10.5 14 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
          <input id="f-q" type="search" value={q} maxLength={100}
            placeholder="Name or ticker" onChange={(e) => setQ(e.target.value)} onBlur={commit} />
        </span>
        {sectors && (
          <span className={fchip(chosenSectors.length > 0)}>
            <label htmlFor="f-sector">Sector</label>
            <select id="f-sector" value="" title={sectors.note} disabled={chosenSectors.length >= MAX_SECTORS}
              onChange={(e) => e.target.value && apply({ sector: [...chosenSectors, e.target.value] })}>
              <option value="">{chosenSectors.length ? "Add a sector" : "Any"}</option>
              {sectors.sectors.filter((s) => !chosenSectors.includes(s.name)).map((s) => (
                <option key={s.name} value={s.name}>{s.name} ({s.companies.toLocaleString()})</option>
              ))}
            </select>
          </span>
        )}
        {/* the sectors' industries are a filter of their own, in the row, not a second box under Sector (Riley 104) */}
        {sectors && offered.length > 0 && (
          <span className={fchip(chosenIndustries.length > 0)}>
            <label htmlFor="f-sector-ind">{chosenSectors.length === 1 ? `Industry in ${chosenSectors[0]}` : "Industry"}</label>
            <select id="f-sector-ind" value="" disabled={chosenIndustries.length >= MAX_INDUSTRIES}
              aria-label={chosenSectors.length === 1 ? "Industry within the sector" : "Industry within the sectors"}
              title="An industry narrows its own sector; a sector with none of its industries chosen counts whole"
              onChange={(e) => e.target.value && apply({ sector_industry: [...chosenIndustries, e.target.value] })}>
              <option value="">{chosenIndustries.length ? "Add an industry" : "Any"}</option>
              {offered.map((s) => {
                const options = s.industries.filter((i) => !chosenIndustries.includes(i.name)).map((i) => (
                  <option key={i.name} value={i.name} title={`SIC ${i.sic.join(", ")}`}>{i.name} ({i.companies.toLocaleString()})</option>
                ));
                return offered.length > 1 ? <optgroup key={s.name} label={s.name}>{options}</optgroup> : <Fragment key={s.name}>{options}</Fragment>;
              })}
            </select>
          </span>
        )}
        <span className={fchip(!!screen.industry)}>
          <label htmlFor="f-ind">SIC industry</label>
          <input id="f-ind" value={industry} maxLength={100} size={8} aria-label="Industry (SIC)"
            placeholder="any" title="Words in the SEC's industry name, e.g. beverages, semiconductors"
            onChange={(e) => setIndustry(e.target.value)} onBlur={commit} />
        </span>
        <span className={fchip(!!screen.band)}>
          <label htmlFor="f-band">Revenue</label>
          <select id="f-band" value={screen.band ?? ""} aria-label="Revenue TTM"
            onChange={(e) => apply({ band: (e.target.value || undefined) as Band | undefined })}>
            <option value="">Any</option>
            {BANDS.map((b) => (
              <option key={b} value={b}>{BAND_LABELS[b]}</option>
            ))}
          </select>
        </span>
        <span className={fchip(screen.growth !== undefined)}>
          <label htmlFor="f-growth">Growth ≥</label>
          <input id="f-growth" type="number" step="any" value={growth} size={4} aria-label="Rev growth YoY, min %"
            placeholder="any" onChange={(e) => setGrowth(e.target.value)} onBlur={commit} />
          <span className="fchip-u" aria-hidden="true">%</span>
        </span>
        <span className={fchip(screen.margin !== undefined)}>
          <label htmlFor="f-margin">Op mgn ≥</label>
          <input id="f-margin" type="number" step="any" value={margin} size={4} aria-label="Op margin TTM, min %"
            placeholder="any" onChange={(e) => setMargin(e.target.value)} onBlur={commit} />
          <span className="fchip-u" aria-hidden="true">%</span>
        </span>
        <span className={fchip(!!screen.exchange)}>
          <label htmlFor="f-ex">Exchange</label>
          <select id="f-ex" value={screen.exchange ?? ""}
            onChange={(e) => apply({ exchange: e.target.value || undefined })}>
            <option value="">Any</option>
            {EXCHANGES.map((x) => (
              <option key={x} value={x}>{x}</option>
            ))}
          </select>
        </span>
        <span className={fchip(!!screen.filer)}>
          <label htmlFor="f-filer">Filer</label>
          <select id="f-filer" value={screen.filer ?? ""} aria-label="Filer category"
            onChange={(e) => apply({ filer: (e.target.value || undefined) as FilerCategory | undefined })}>
            <option value="">Any</option>
            {FILER_CATEGORIES.map((c) => (
              <option key={c} value={c}>{FILER_LABELS[c]}</option>
            ))}
          </select>
        </span>
        <span className={fchip(!!screen.fye)}>
          <label htmlFor="f-fye">FYE</label>
          <select id="f-fye" value={screen.fye ?? ""} aria-label="Fiscal year end"
            onChange={(e) => apply({ fye: e.target.value ? Number(e.target.value) : undefined })}>
            <option value="">Any</option>
            {MONTHS.map((m, i) => (
              <option key={m} value={i + 1}>{m}</option>
            ))}
          </select>
        </span>
        {active > 0 && (
          <button type="button" className="btn ghost filter-clear" onClick={() => go({ view: "companies" })}>
            Clear
          </button>
        )}
        <button type="submit" className="visually-hidden">Apply</button>
      </form>
      {applied > 0 && (
        <ul className="applied" aria-label="Applied filters">
          {screen.issue && (
            <li className="applied-issue">
              <span className="pencil">From the data status page:</span>
              <button type="button" className="chip removable" onClick={() => apply({ issue: undefined })}
                title="Remove the data-check filter">
                {checkName(screen.issue) ?? screen.issue} ✕
              </button>
              <span className="pencil">Each company's Data notes tab shows what was found. Only companies you can read are listed.</span>
            </li>
          )}
          {screen.sic && (
            <li>
              <button type="button" className="chip removable" onClick={() => apply({ sic: undefined })}
                title="Remove the SIC filter">
                SIC {screen.sic} ✕
              </button>
            </li>
          )}
          {chosenSectors.map((s) => (
            <li key={s}>
              <button type="button" className="chip removable" title={`Remove ${s} and its industries`}
                onClick={() => apply({ sector: without(chosenSectors, s), sector_industry: chosenIndustries.filter((i) => !industriesOf(s).includes(i)) })}>
                {s} ✕
              </button>
            </li>
          ))}
          {chosenIndustries.map((i) => (
            <li key={i}>
              <button type="button" className="chip removable" title={`Remove ${i}`}
                onClick={() => apply({ sector_industry: without(chosenIndustries, i) })}>
                {i} ✕
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Table({
  updating = false,
  rows,
  columns,
  sort,
  watched,
  onSort,
  onSic,
  onWatch,
  onChart,
}: {
  /** the last screen's rows while a new one runs: dimmed, marked busy */
  updating?: boolean;
  rows: ReturnType<typeof screenRow>[];
  columns: ColumnSpec[];
  sort: string;
  watched: Set<string> | null;
  onSort: (sort: string) => void;
  onSic: (sic: string) => void;
  onWatch: (companyId: string, watch: boolean) => Promise<void>;
  onChart?: (companyId: string) => void;
}) {
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const desc = sort.startsWith("-");
  const sortKey = sort.replace(/^-/, "");

  /** Click a header: sort by it, or flip direction if it already is. Values start high-first. */
  const th = (key: string, numeric: boolean) => ({
    onClick: () => onSort(sortKey === key ? (desc ? key : `-${key}`) : numeric ? `-${key}` : key),
    "aria-sort": (sortKey === key ? (desc ? "descending" : "ascending") : "none") as
      "descending" | "ascending" | "none",
  });
  const arrow = (key: string) => (sortKey === key ? (desc ? " ↓" : " ↑") : "");

  const toggle = (id: string, watch: boolean) => {
    setPending(id);
    setError(null);
    onWatch(id, watch)
      .catch(() => setError("Couldn't update the watchlist. Try again."))
      .finally(() => setPending(null));
  };

  return (
    <>
      {error && <p className="empty error">{error}</p>}
      <TableScroll label="Screener results">
        <table className={updating ? "t screen updating" : "t screen"} aria-busy={updating || undefined}>
          <thead>
            <tr>
              <th className="first sort" {...th("name", false)}>Company{arrow("name")}</th>
              <th className="ind sort" {...th("sic", false)}>Industry (SIC){arrow("sic")}</th>
              {columns.map((c) =>
                sortable(c.key) ? (
                  <th key={c.key} className={c.label.length > 14 ? "n long sort" : "n sort"} {...th(c.key, true)}>
                    {c.label}{arrow(c.key)} <span className="unit">{c.period}</span>
                  </th>
                ) : (
                  <th key={c.key} className={c.label.length > 14 ? "n long" : "n"} title={c.long}>
                    {c.label} <span className="unit">{c.period}</span>
                  </th>
                ),
              )}
              <th className="sort" {...th("fiscal_year_end", false)}>FYE{arrow("fiscal_year_end")}</th>
              <th>
                <span className="visually-hidden">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody onKeyDown={onTableKey}>
            {rows.map((r, i) => {
              const isWatched = watched?.has(r.companyId) ?? false;
              const group = sortKey === GROUP_SORT ? `${r.sector ?? "No sector (no SIC code)"}${r.sectorIndustry ? `: ${r.sectorIndustry}` : ""}` : null;
              const newGroup = group !== null && (i === 0 || group !== `${rows[i - 1]!.sector ?? "No sector (no SIC code)"}${rows[i - 1]!.sectorIndustry ? `: ${rows[i - 1]!.sectorIndustry}` : ""}`);
              const joint = coRegistrantNote(r.name, r.coRegistrants, r.companyId);
              return (
                <Fragment key={r.companyId}>
                {newGroup && (
                  <tr className="group-head">
                    <th colSpan={columns.length + 4} scope="rowgroup">{group}</th>
                  </tr>
                )}
                <tr className={[r.readable ? "clk" : "clk masked", isWatched && "watched"].filter(Boolean).join(" ")} {...navRow(i, r.name)}
                  onClick={() => go({ view: "company", companyId: r.companyId })}>
                  <td className="first">
                    <a className="co" href={href({ view: "company", companyId: r.companyId })}
                      onClick={(e) => e.stopPropagation()}>
                      <b title={r.name} data-entity-name="">{r.name}</b>
                      {r.ticker && <span className="ticker">{r.ticker}</span>}
                      {!r.readable && (
                        <span className="tag" title={MASKED_TITLE}>metadata only</span>
                      )}
                      {joint && <span className="tag joint" title={joint}>combined 10-K</span>}
                    </a>
                    {r.industry && <div className="co-sub faint small" title={r.industry}>{r.industry}</div>}
                    {/* 074: only a mark worth a second look shows in the list (high says nothing), under the industry, its
                        reasons a tap away (Riley, test 10, finding 47: squeezed beside the ticker, hover only) */}
                    {r.confidence && r.confidence.level !== "high" && (
                      <div className="co-conf small">
                        <Why className="co-confidence" label={confidenceWords(r.confidence).label} why={confidenceWords(r.confidence).hover} />
                      </div>
                    )}
                  </td>
                  <td className="muted ind" title={`${r.sector ? `${r.sector} · ${r.sectorIndustry} (Tributary's grouping). ` : ""}SIC: ${r.industry ?? "none"}`}>
                    <span className="ind-in">
                      <span className="ind-name">
                        {r.industry ?? "—"}
                        {r.sectorIndustry && <span className="faint small sector-sub">{r.sectorIndustry}</span>}
                      </span>
                      {r.sic && (
                        <button className="link faint sic-code" title={`Only SIC ${r.sic}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            onSic(r.sic!);
                          }}>
                          {r.sic}
                        </button>
                      )}
                    </span>
                  </td>
                  {columns.map((c) => {
                    const cell = r.cells[c.key]!;
                    // a reading is a dash and a letter (§4), explained under the table; a change takes its
                    // direction's colour and a true minus (§2.3), a level none
                    const letter = r.readable ? cellLetter(cell) : null;
                    const tone = cell.value === null
                      ? "faint"
                      : c.kind === "growth"
                        ? cell.value >= 0 ? "gain" : "loss"
                        : "";
                    return (
                      <td key={c.key} className={`n ${tone}`} title={cell.title}>
                        {letter && letter !== "c" ? (
                          <span className="rdg-cell">{NOT_REPORTED}<sup className="rdg">{letter}</sup></span>
                        ) : letter === "c" ? (
                          <span className="rdg-cell">{paren(cell.text)}<sup className="rdg">c</sup></span>
                        ) : (
                          /* a negative amount in parentheses, as a ledger prints it (the hover and CSV keep the
                             sign); a fall in growth takes a true minus */
                          <FigureText cell={c.kind === "usd" ? { ...cell, text: paren(cell.text) }
                            : c.kind === "growth" ? { ...cell, text: cell.text.replace(/^-/, "−") } : cell} />
                        )}
                      </td>
                    );
                  })}
                  <td className="muted" title={r.fye ?? undefined}>{r.fye?.slice(0, 3) ?? "—"}</td>
                  <td className="n actions" onClick={(e) => e.stopPropagation()}>
                    {r.readable && onChart && (
                      <button className="btn sm ghost" onClick={() => onChart(r.companyId)}
                        aria-label={`Chart ${r.name}`}>
                        Chart
                      </button>
                    )}
                    {watched && (
                      <button
                        className="btn sm ghost"
                        disabled={pending === r.companyId}
                        onClick={() => toggle(r.companyId, !isWatched)}
                        aria-label={`${isWatched ? "Remove" : "Add"} ${r.name} ${isWatched ? "from" : "to"} watchlist`}
                        title={r.readable ? undefined : "Watching follows its filings; it grants no access to values"}
                      >
                        {isWatched ? "✓ Watching" : "+ Watch"}
                      </button>
                    )}
                  </td>
                </tr>
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </TableScroll>
    </>
  );
}

function Pager({
  offset,
  shown,
  total,
  onPage,
}: {
  offset: number;
  shown: number;
  total: number | null;
  onPage: (page: number) => void;
}) {
  const page = Math.floor(offset / PAGE_SIZE);
  const more = total === null ? shown === PAGE_SIZE : offset + shown < total;
  if (page === 0 && !more) return null;
  return (
    <div className="pager">
      <span className="muted">
        {offset + 1}–{offset + shown}
        {total !== null && ` of ${total.toLocaleString()}`}
      </span>
      <button className="btn sm" disabled={page === 0} onClick={() => onPage(page - 1)}>
        Previous
      </button>
      <button className="btn sm" disabled={!more} onClick={() => onPage(page + 1)}>
        Next
      </button>
    </div>
  );
}

import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";

import { failureReason, screener, SCREENER_CSV_ROWS, screenerCsv } from "../api";
import { DEFAULT_SPECS, MASKED_TITLE, screenRow } from "../companies/rows";
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
  hasValueFilter,
  isEmpty,
  MONTHS,
  PAGE_SIZE,
  screenParams,
  sortable,
  type Band,
  type FilerCategory,
  type ScreenFilters,
} from "../companies/screen";
import { FigureText } from "../components/FigureText";
import { ErrorPanel, Skeleton } from "../components/States";
import { navRow, onTableKey } from "../components/tableNav";
import { download } from "../download";
import { go, href } from "../route";
import { useScope } from "../scope";
import type { Screener } from "../types";
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
}

type State =
  | { state: "loading" }
  | { state: "ok"; data: Screener }
  | { state: "error"; message: string };

/** Companies: the screener over /v1/screener. Filters live in the URL, so a screen can be
 * bookmarked, shared and undone with Back. In a demo sandbox (scope.ts) it screens only the
 * demo's companies (`access=financials`, the API's own filter), so "only companies I can
 * read" is always on and not offered. */
export function Companies({ token, screen: asked, watched, onWatch, onChart, saveBar, columns = DEFAULT_SPECS, columnPicker }: Props) {
  const scope = useScope();
  // in a sandbox the filters shown never include "mine": it is implied
  const screen = useMemo(() => (scope ? { ...asked, mine: undefined } : asked), [scope, asked]);
  const [result, setResult] = useState<State>({ state: "loading" });
  const [exporting, setExporting] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const columnKeys = columns.map((c) => c.key).join(",");
  const params = useMemo(
    () => screenParams(scope ? { ...screen, mine: true } : screen, columnKeys.split(",")),
    [scope, screen, columnKeys],
  );
  const key = JSON.stringify(params);

  useEffect(() => {
    let live = true;
    setResult((r) => (r.state === "ok" ? r : { state: "loading" }));
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

  const set = (patch: Partial<ScreenFilters>) =>
    go({ view: "companies", screen: { ...screen, page: undefined, ...patch } });

  const exportCsv = () => {
    setExporting(true);
    setNote(null);
    screenerCsv(token, params)
      .then((blob) => download("screener.csv", blob))
      .catch(() => setNote("Couldn't export the screen. Try again."))
      .finally(() => setExporting(false));
  };

  const data = result.state === "ok" ? result.data : null;
  const rows = useMemo(() => (data ? data.rows.map((r) => screenRow(r, columns)) : []), [data, columns]);
  const masked = rows.filter((r) => !r.readable).length;
  const total = data?.total ?? null;

  return (
    <div className="page">
      <div className="row page-bar">
        <h1 className="grow">Companies</h1>
        {total !== null && (
          <span className="muted">
            {scope
              ? isEmpty(screen)
                ? `${total} ${total === 1 ? "company" : "companies"} in your demo`
                : `${total} of the ${scope.ids.size} companies in your demo`
              : `${total.toLocaleString()} ${total === 1 ? "company" : "companies"}${countWords(screen)}`}
          </span>
        )}
        <button
          className="btn"
          onClick={exportCsv}
          disabled={exporting || !data || total === 0}
          title={`The whole screen, up to ${SCREENER_CSV_ROWS.toLocaleString()} rows, exact values with their period ends`}
        >
          {exporting ? "Exporting…" : "Export CSV"}
        </button>
        {columnPicker}
        {saveBar}
      </div>
      {note && <p className="empty error">{note}</p>}

      <div className="screener">
        <FilterRail screen={screen} set={set} scoped={scope !== null} />

        <section className="card">
          <div className="card-h keep">
            <h2>Screen</h2>
            <span className="sub">{describe(screen)}</span>
            <span className="grow" />
            {!isEmpty(screen) && (
              <button className="btn sm ghost" onClick={() => go({ view: "companies" })}>
                Reset filters
              </button>
            )}
          </div>
          {result.state === "error" ? (
            <ErrorPanel what="this screen" detail={`${result.message}. Fewer columns or filters may help`}
              onRetry={() => {
                setResult({ state: "loading" });
                setAttempt((n) => n + 1);
              }} />
          ) : result.state === "loading" ? (
            <Skeleton lines={8} label="Running the screen" />
          ) : (
            <>
              <Table
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
          <div className="foot">
            USD. TTM = trailing twelve months to each company's latest reported quarter; net debt
            and debt/equity at that quarter's end. Hover a value for its period and source filing.
            A figure from an earlier period than the company's latest shows its date; about a year or
            more behind it reads old, dimmed, and leaves sorts and filters. n/m = not meaningful,
            n/a = doesn't apply, — = not reported; hover for why.
            Keyboard: ↑ ↓ move between rows, Home/End jump, Enter opens the company.
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
          </div>
        </section>
      </div>
    </div>
  );
}

/** A typed filter applies this long after the last keystroke. */
const APPLY_AFTER_MS = 400;
/** Up to this wide the filters start folded: all nine would fill a phone's first screen. */
const FOLD_FILTERS_PX = 700;

function FilterRail({ screen, set, scoped }: {
  screen: ScreenFilters;
  set: (p: Partial<ScreenFilters>) => void;
  /** a demo sandbox: only its companies, so no "Only companies I can read" */
  scoped: boolean;
}) {
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

  return (
    <details className="card filter-band" open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>
        <b>Filters</b>
        <span className="faint small">{active ? `${active} active` : "none set"} · apply as you change them</span>
      </summary>
      <form className="filters" onSubmit={submit} aria-label="Screen filters">
      {screen.issue && (
        <div className="filter-note">
          <span className="small muted">From the data status page</span>
          <button type="button" className="chip removable" onClick={() => apply({ issue: undefined })}
            title="Remove the data-check filter">
            {checkName(screen.issue) ?? screen.issue} ✕
          </button>
          <p className="faint small">
            Each company's Data notes tab shows what was found. Only companies you can read are listed.
          </p>
        </div>
      )}
      <div>
        <label htmlFor="f-q">Name or ticker</label>
        <input id="f-q" className="field" type="search" value={q} maxLength={100}
          placeholder="e.g. PEP or Pepsi" onChange={(e) => setQ(e.target.value)} onBlur={commit} />
      </div>
      <div>
        <label htmlFor="f-ind">Industry (SIC)</label>
        <input id="f-ind" className="field" value={industry} maxLength={100}
          placeholder="e.g. beverages, semiconductors" onChange={(e) => setIndustry(e.target.value)}
          onBlur={commit} />
        {screen.sic && (
          <button type="button" className="chip removable" onClick={() => apply({ sic: undefined })}
            title="Remove the SIC filter">
            SIC {screen.sic} ✕
          </button>
        )}
      </div>
      <div>
        <label htmlFor="f-band">Revenue TTM</label>
        <select id="f-band" className="field" value={screen.band ?? ""}
          onChange={(e) => apply({ band: (e.target.value || undefined) as Band | undefined })}>
          <option value="">Any</option>
          {BANDS.map((b) => (
            <option key={b} value={b}>{BAND_LABELS[b]}</option>
          ))}
        </select>
      </div>
      <div>
        <label htmlFor="f-growth">Rev growth YoY, min %</label>
        <input id="f-growth" className="field" type="number" step="any" value={growth}
          placeholder="any" onChange={(e) => setGrowth(e.target.value)} onBlur={commit} />
      </div>
      <div>
        <label htmlFor="f-margin">Op margin TTM, min %</label>
        <input id="f-margin" className="field" type="number" step="any" value={margin}
          placeholder="any" onChange={(e) => setMargin(e.target.value)} onBlur={commit} />
      </div>
      <div>
        <label htmlFor="f-fye">Fiscal year end</label>
        <select id="f-fye" className="field" value={screen.fye ?? ""}
          onChange={(e) => apply({ fye: e.target.value ? Number(e.target.value) : undefined })}>
          <option value="">Any</option>
          {MONTHS.map((m, i) => (
            <option key={m} value={i + 1}>{m}</option>
          ))}
        </select>
      </div>
      <div>
        <label htmlFor="f-ex">Exchange</label>
        <select id="f-ex" className="field" value={screen.exchange ?? ""}
          onChange={(e) => apply({ exchange: e.target.value || undefined })}>
          <option value="">Any</option>
          {EXCHANGES.map((x) => (
            <option key={x} value={x}>{x}</option>
          ))}
        </select>
      </div>
      <div>
        <label htmlFor="f-filer">Filer category</label>
        <select id="f-filer" className="field" value={screen.filer ?? ""}
          onChange={(e) => apply({ filer: (e.target.value || undefined) as FilerCategory | undefined })}>
          <option value="">Any</option>
          {FILER_CATEGORIES.map((c) => (
            <option key={c} value={c}>{FILER_LABELS[c]}</option>
          ))}
        </select>
      </div>
      {!scoped && (
        <label className="chk">
          <input type="checkbox" checked={screen.mine ?? false}
            onChange={(e) => apply({ mine: e.target.checked || undefined })} />
          Only companies I can read
        </label>
      )}
      <button type="submit" className="visually-hidden">Apply</button>
      </form>
    </details>
  );
}

function Table({
  rows,
  columns,
  sort,
  watched,
  onSort,
  onSic,
  onWatch,
  onChart,
}: {
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
        <table className="t screen">
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
              return (
                <tr key={r.companyId} className={r.readable ? "clk" : "clk masked"} {...navRow(i, r.name)}
                  onClick={() => go({ view: "company", companyId: r.companyId })}>
                  <td className="first">
                    <a className="co" href={href({ view: "company", companyId: r.companyId })}
                      onClick={(e) => e.stopPropagation()}>
                      <b title={r.name}>{r.name}</b>
                      {r.ticker && <span className="faint mono">{r.ticker}</span>}
                      {!r.readable && (
                        <span className="tag" title={MASKED_TITLE}>metadata only</span>
                      )}
                    </a>
                    {r.industry && <div className="co-sub faint small" title={r.industry}>{r.industry}</div>}
                  </td>
                  <td className="muted ind" title={r.industry ?? undefined}>
                    <span className="ind-in">
                      <span className="ind-name">{r.industry ?? "—"}</span>
                      {r.sic && (
                        <button className="link faint mono" title={`Only SIC ${r.sic}`}
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
                    const tone = cell.value === null
                      ? "faint"
                      : c.kind === "growth"
                        ? cell.value >= 0 ? "pos" : "neg"
                        : "";
                    return (
                      <td key={c.key} className={`n ${tone}`} title={cell.title}>
                        <FigureText cell={cell} />
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

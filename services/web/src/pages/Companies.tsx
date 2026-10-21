import { useEffect, useMemo, useState, type FormEvent } from "react";

import { ApiError, screener, screenerCsv, SCREENER_CSV_ROWS } from "../api";
import { MASKED_TITLE, screenRow } from "../companies/rows";
import {
  BAND_LABELS,
  BANDS,
  DEFAULT_SORT,
  describe,
  EXCHANGES,
  FILER_CATEGORIES,
  FILER_LABELS,
  hasValueFilter,
  isEmpty,
  MONTHS,
  PAGE_SIZE,
  SCREEN_COLUMNS,
  screenParams,
  type Band,
  type FilerCategory,
  type ScreenFilters,
} from "../companies/screen";
import { download } from "../download";
import { go, href } from "../route";
import type { Screener } from "../types";

interface Props {
  token: string;
  screen: ScreenFilters;
  /** company ids on the user's watchlist (null while it loads or is unavailable) */
  watched: Set<string> | null;
  onWatch: (companyId: string, watch: boolean) => Promise<void>;
  onChart?: (companyId: string, name: string) => void;
}

type State =
  | { state: "loading" }
  | { state: "ok"; data: Screener }
  | { state: "error"; message: string };

/** Companies: the screener over /v1/screener. Filters live in the URL, so a screen can be
 * bookmarked, shared and undone with Back. */
export function Companies({ token, screen, watched, onWatch, onChart }: Props) {
  const [result, setResult] = useState<State>({ state: "loading" });
  const [exporting, setExporting] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const params = useMemo(() => screenParams(screen), [screen]);
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
            message: e instanceof ApiError ? e.message : "the Query API didn't answer",
          }),
      );
    return () => {
      live = false;
    };
    // params is keyed by its JSON so a new but equal screen doesn't refetch
  }, [token, key]);

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
  const rows = useMemo(() => (data ? data.rows.map(screenRow) : []), [data]);
  const masked = rows.filter((r) => !r.readable).length;
  const total = data?.total ?? null;

  return (
    <div className="page">
      <div className="row">
        <h1 className="grow">Companies</h1>
        {total !== null && (
          <span className="muted">
            {total.toLocaleString()} {total === 1 ? "company" : "companies"} with financials
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
      </div>
      {note && <p className="empty error">{note}</p>}

      <div className="screener">
        <FilterRail key={JSON.stringify(screen)} screen={screen} set={set} />

        <section className="card grow">
          <div className="card-h">
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
            <p className="empty error">Couldn't run this screen ({result.message}).</p>
          ) : result.state === "loading" ? (
            <p className="empty">Running the screen…</p>
          ) : rows.length === 0 ? (
            <p className="empty">
              No companies match.{" "}
              {hasValueFilter(screen)
                ? "Value filters only match companies your plan covers; loosen a filter or clear it."
                : "Loosen a filter."}
            </p>
          ) : (
            <Table
              rows={rows}
              sort={screen.sort ?? DEFAULT_SORT}
              watched={watched}
              onSort={(sort) => set({ sort })}
              onSic={(sic) => set({ sic, industry: undefined })}
              onWatch={onWatch}
              onChart={onChart}
            />
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
            {hasValueFilter(screen) &&
              " Value filters and size bands only match companies whose financials you can read."}
          </div>
        </section>
      </div>
    </div>
  );
}

function FilterRail({ screen, set }: { screen: ScreenFilters; set: (p: Partial<ScreenFilters>) => void }) {
  // text and number inputs apply on Enter or when they lose focus; selects apply at once
  const [q, setQ] = useState(screen.q ?? "");
  const [industry, setIndustry] = useState(screen.industry ?? "");
  const [growth, setGrowth] = useState(screen.growth?.toString() ?? "");
  const [margin, setMargin] = useState(screen.margin?.toString() ?? "");

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

  return (
    <form className="card card-b filters" onSubmit={submit} aria-label="Screen filters">
      <div>
        <label htmlFor="f-q">Name or ticker</label>
        <input id="f-q" className="field" type="search" value={q} maxLength={100}
          placeholder="e.g. PEP or Pepsi" onChange={(e) => setQ(e.target.value)} onBlur={commit} />
      </div>
      <div>
        <label htmlFor="f-ind">Industry (SIC description)</label>
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
        <label htmlFor="f-band">Size: revenue TTM</label>
        <select id="f-band" className="field" value={screen.band ?? ""}
          onChange={(e) => apply({ band: (e.target.value || undefined) as Band | undefined })}>
          <option value="">Any</option>
          {BANDS.map((b) => (
            <option key={b} value={b}>{BAND_LABELS[b]}</option>
          ))}
        </select>
      </div>
      <div>
        <label htmlFor="f-growth">Revenue growth YoY at least (%)</label>
        <input id="f-growth" className="field" type="number" step="any" value={growth}
          placeholder="any" onChange={(e) => setGrowth(e.target.value)} onBlur={commit} />
      </div>
      <div>
        <label htmlFor="f-margin">Operating margin TTM at least (%)</label>
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
      <label className="chk">
        <input type="checkbox" checked={screen.mine ?? false}
          onChange={(e) => apply({ mine: e.target.checked || undefined })} />
        Only companies I can read
      </label>
      <button type="submit" className="visually-hidden">Apply</button>
    </form>
  );
}

function Table({
  rows,
  sort,
  watched,
  onSort,
  onSic,
  onWatch,
  onChart,
}: {
  rows: ReturnType<typeof screenRow>[];
  sort: string;
  watched: Set<string> | null;
  onSort: (sort: string) => void;
  onSic: (sic: string) => void;
  onWatch: (companyId: string, watch: boolean) => Promise<void>;
  onChart?: (companyId: string, name: string) => void;
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
      <div className="tw">
        <table className="t screen">
          <thead>
            <tr>
              <th className="first sort" {...th("name", false)}>Company{arrow("name")}</th>
              <th className="sort" {...th("sic", false)}>Industry (SIC){arrow("sic")}</th>
              {SCREEN_COLUMNS.map((c) => (
                <th key={c.key} className="n sort" {...th(c.key, true)}>
                  {c.label} <span className="unit">{c.period}</span>
                  {arrow(c.key)}
                </th>
              ))}
              <th className="sort" {...th("fiscal_year_end", false)}>FYE{arrow("fiscal_year_end")}</th>
              <th>
                <span className="visually-hidden">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const isWatched = watched?.has(r.companyId) ?? false;
              return (
                <tr key={r.companyId} className={r.readable ? "clk" : "clk masked"}
                  onClick={() => go({ view: "company", companyId: r.companyId })}>
                  <td className="first">
                    <a className="co" href={href({ view: "company", companyId: r.companyId })}
                      onClick={(e) => e.stopPropagation()}>
                      <b>{r.name}</b>
                      {r.ticker && <span className="faint mono">{r.ticker}</span>}
                      {!r.readable && (
                        <span className="tag" title={MASKED_TITLE}>metadata only</span>
                      )}
                    </a>
                  </td>
                  <td className="muted">
                    {r.industry ?? "—"}{" "}
                    {r.sic && (
                      <button className="link faint mono" title={`Only SIC ${r.sic}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          onSic(r.sic!);
                        }}>
                        {r.sic}
                      </button>
                    )}
                  </td>
                  {SCREEN_COLUMNS.map((c) => {
                    const cell = r.cells[c.key]!;
                    const tone = cell.value === null
                      ? "faint"
                      : c.kind === "growth"
                        ? cell.value >= 0 ? "pos" : "neg"
                        : "";
                    return (
                      <td key={c.key} className={`n ${tone}`} title={cell.title}>
                        {cell.text}
                      </td>
                    );
                  })}
                  <td className="muted">{r.fye ?? "—"}</td>
                  <td className="n actions" onClick={(e) => e.stopPropagation()}>
                    {r.readable && onChart && (
                      <button className="btn sm ghost" onClick={() => onChart(r.companyId, r.name)}
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
      </div>
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

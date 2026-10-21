/** Screener filter state: what the Companies URL holds (`#/companies?band=1b-10b&margin=20`)
 * and how it maps to the Query API's /v1/screener parameters. `parseScreen` drops anything it
 * doesn't recognize, so a hand-edited URL can't send the API a malformed filter. */

export const BANDS = ["lt100m", "100m-1b", "1b-10b", "10b-100b", "gte100b"] as const;
export type Band = (typeof BANDS)[number];
export const BAND_LABELS: Record<Band, string> = {
  lt100m: "Under $100M",
  "100m-1b": "$100M to $1B",
  "1b-10b": "$1B to $10B",
  "10b-100b": "$10B to $100B",
  gte100b: "$100B and over",
};

export const FILER_CATEGORIES = ["large_accelerated", "accelerated", "non_accelerated"] as const;
export type FilerCategory = (typeof FILER_CATEGORIES)[number];
export const FILER_LABELS: Record<FilerCategory, string> = {
  large_accelerated: "Large accelerated",
  accelerated: "Accelerated",
  non_accelerated: "Non-accelerated",
};

export const EXCHANGES = ["NYSE", "Nasdaq", "CBOE", "OTC"] as const;

/** Value columns the screener shows, in order. Ratios are fractions in the API. */
export const SCREEN_COLUMNS: { key: string; label: string; period: string; kind: "usd" | "pct" | "growth" | "x" }[] = [
  { key: "revenue_ttm", label: "Revenue", period: "TTM", kind: "usd" },
  { key: "revenue_growth_ttm", label: "Rev growth", period: "TTM YoY", kind: "growth" },
  { key: "gross_margin_ttm", label: "Gross mgn", period: "TTM", kind: "pct" },
  { key: "operating_margin_ttm", label: "Op mgn", period: "TTM", kind: "pct" },
  { key: "free_cash_flow_ttm", label: "FCF", period: "TTM", kind: "usd" },
  { key: "net_debt_q", label: "Net debt", period: "qtr end", kind: "usd" },
  { key: "debt_to_equity_q", label: "Debt/eq", period: "qtr end", kind: "x" },
];

/** Sorts on the Company and Industry columns; the value columns sort by their key. */
const META_SORTS = new Set(["name", "sic", "fiscal_year_end"]);
const SORTS = new Set([...META_SORTS, ...SCREEN_COLUMNS.map((c) => c.key)]);
export const DEFAULT_SORT = "-revenue_ttm";
export const PAGE_SIZE = 50;

export interface ScreenFilters {
  /** name contains, or exact ticker */
  q?: string;
  /** SIC description contains */
  industry?: string;
  /** one 4-digit SIC code (from clicking a row's industry) */
  sic?: string;
  band?: Band;
  /** minimum revenue growth, TTM YoY, in percent */
  growth?: number;
  /** minimum operating margin, TTM, in percent */
  margin?: number;
  /** fiscal year end month, 1-12 */
  fye?: number;
  exchange?: string;
  filer?: FilerCategory;
  /** only companies whose financials the user can read */
  mine?: boolean;
  sort?: string;
  /** 0-based page of PAGE_SIZE rows */
  page?: number;
}

const KEYS = ["q", "industry", "sic", "band", "growth", "margin", "fye", "exchange", "filer",
  "mine", "sort", "page"] as const;

function text(v: string | null, max = 100): string | undefined {
  const t = v?.trim();
  return t ? t.slice(0, max) : undefined;
}

function num(v: string | null, lo: number, hi: number): number | undefined {
  if (v === null || v.trim() === "") return undefined;
  const n = Number(v);
  return Number.isFinite(n) && n >= lo && n <= hi ? n : undefined;
}

function int(v: string | null, lo: number, hi: number): number | undefined {
  const n = num(v, lo, hi);
  return n !== undefined && Number.isInteger(n) ? n : undefined;
}

export function parseScreen(query: string): ScreenFilters {
  const p = new URLSearchParams(query);
  const f: ScreenFilters = {
    q: text(p.get("q")),
    industry: text(p.get("industry")),
    sic: /^\d{4}$/.test(p.get("sic") ?? "") ? p.get("sic")! : undefined,
    band: (BANDS as readonly string[]).includes(p.get("band") ?? "") ? (p.get("band") as Band) : undefined,
    growth: num(p.get("growth"), -1000, 100000),
    margin: num(p.get("margin"), -1000, 100),
    fye: int(p.get("fye"), 1, 12),
    exchange: text(p.get("exchange"), 20),
    filer: (FILER_CATEGORIES as readonly string[]).includes(p.get("filer") ?? "")
      ? (p.get("filer") as FilerCategory)
      : undefined,
    mine: p.get("mine") === "1" ? true : undefined,
    sort: SORTS.has((p.get("sort") ?? "").replace(/^-/, "")) ? p.get("sort")! : undefined,
    page: int(p.get("page"), 1, 10000),
  };
  return clean(f);
}

/** Drops unset fields and defaults, so equal screens serialize equally. */
export function clean(f: ScreenFilters): ScreenFilters {
  const out: ScreenFilters = {};
  for (const k of KEYS) {
    const v = f[k];
    if (v === undefined || v === "" || v === false) continue;
    if (k === "sort" && v === DEFAULT_SORT) continue;
    if (k === "page" && v === 0) continue;
    (out as Record<string, unknown>)[k] = v;
  }
  return out;
}

/** The URL query for a screen, in a fixed key order ("" when nothing is set). */
export function screenQuery(f: ScreenFilters): string {
  const c = clean(f);
  const p = new URLSearchParams();
  for (const k of KEYS) {
    const v = c[k];
    if (v !== undefined) p.set(k, v === true ? "1" : String(v));
  }
  return p.toString();
}

export function isEmpty(f: ScreenFilters): boolean {
  return screenQuery({ ...f, sort: undefined, page: undefined }) === "";
}

/** Percent → fraction as a decimal string without float noise: 12.5 → "0.125". */
export function fraction(percent: number): string {
  return String(Number((percent / 100).toPrecision(12)));
}

type Params = Record<string, string | number | string[]>;

/** /v1/screener query parameters for a screen. */
export function screenParams(f: ScreenFilters): Params {
  const where: string[] = [];
  if (f.growth !== undefined) where.push(`revenue_growth_ttm:gte:${fraction(f.growth)}`);
  if (f.margin !== undefined) where.push(`operating_margin_ttm:gte:${fraction(f.margin)}`);
  const p: Params = {
    columns: SCREEN_COLUMNS.map((c) => c.key),
    sort: f.sort ?? DEFAULT_SORT,
    limit: PAGE_SIZE,
    offset: (f.page ?? 0) * PAGE_SIZE,
  };
  if (where.length) p.where = where;
  if (f.q) p.q = f.q;
  if (f.industry) p.industry = f.industry;
  if (f.sic) p.sic = [f.sic];
  if (f.band) p.revenue_band = f.band;
  if (f.fye) p.fiscal_year_end_month = f.fye;
  if (f.exchange) p.exchange = [f.exchange];
  if (f.filer) p.filer_category = [f.filer];
  if (f.mine) p.access = "financials";
  return p;
}

/** Whether a screen filters on values: ungranted companies can't match those. */
export function hasValueFilter(f: ScreenFilters): boolean {
  return f.growth !== undefined || f.margin !== undefined || f.band !== undefined;
}

/** A short description for the assistant's "Applied" line and the page subtitle. */
export function describe(f: ScreenFilters): string {
  const parts = [
    f.q && `name or ticker "${f.q}"`,
    f.industry && `industry contains "${f.industry}"`,
    f.sic && `SIC ${f.sic}`,
    f.band && `revenue ${BAND_LABELS[f.band]}`,
    f.growth !== undefined && `revenue growth ≥ ${f.growth}%`,
    f.margin !== undefined && `operating margin ≥ ${f.margin}%`,
    f.fye && `fiscal year ends in ${MONTHS[f.fye - 1]}`,
    f.exchange && `on ${f.exchange}`,
    f.filer && FILER_LABELS[f.filer].toLowerCase() + " filers",
    f.mine && "only companies you can read",
  ].filter(Boolean);
  return parts.length ? parts.join(", ") : "no filters";
}

export const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
  "September", "October", "November", "December"];

/** SEC fiscal year end "0126" (MMDD) → "January". */
export function fyeMonth(fye: string | null): string | null {
  const m = Number(fye?.slice(0, 2));
  return m >= 1 && m <= 12 ? MONTHS[m - 1]! : null;
}

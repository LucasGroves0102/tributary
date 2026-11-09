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

/** What each data-quality check flags (migrations 014, 018, 023: ops.check_financials). The status
 * page's rows, and the screener's "with a finding of this check" filter. */
export const CHECKS: Record<string, string> = {
  impossible_value: "Impossible values: negative revenue, assets, debt or share counts, or a part larger than its total",
  overlapping_quarters: "A quarter starting before the previous one ended",
  calendar_collision: "Two quarters of a metric in one calendar quarter",
  annual_sum: "Four quarters differing from the reported year by more than 1%",
  series_gap: "A quarter missing after four contiguous ones",
  sign_flip: "An expense or outflow whose sign differs from both neighbouring quarters",
  outlier: "A one-quarter spike of 10× or more: seasonal, a one-off, or a scale error in the filing",
  mistagged_fact: "A figure kept out of the data: a period ending after its filing, or debt above total liabilities",
  net_debt_gap: "Debt and cash on file but no net debt: investments or debt can't be told apart from a gap",
  segment_sum_business_segment: "Business segments not adding up to the company total (±1%)",
  segment_sum_geography: "Geographic segments not adding up to the company total (±1%)",
  segment_sum_product_service: "Product and service lines not adding up to the company total (±1%)",
  restatement: "A figure a later filing changed by more than 0.5%",
};

/** A check's description, or undefined when `check` isn't one: own keys only, so an address's
 * `issue=constructor` or `toString` (Object.prototype's) is no check. */
export function checkName(check: string | null | undefined): string | undefined {
  return check && Object.prototype.hasOwnProperty.call(CHECKS, check) ? CHECKS[check] : undefined;
}

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
  /** a data check's name (CHECKS): companies with a finding of it, from the status page */
  issue?: string;
  /** only companies whose financials the user can read */
  mine?: boolean;
  sort?: string;
  /** 0-based page of PAGE_SIZE rows */
  page?: number;
}

const KEYS = ["q", "industry", "sic", "band", "growth", "margin", "fye", "exchange", "filer", "issue",
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
    issue: checkName(p.get("issue")) ? p.get("issue")! : undefined,
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

/** How many filters are set: the count on the collapsed filter band. */
export function activeFilters(f: ScreenFilters): number {
  return [f.q, f.industry, f.sic, f.band, f.growth, f.margin, f.fye, f.exchange, f.filer, f.issue, f.mine]
    .filter((v) => v !== undefined && v !== "" && v !== false).length;
}

/** Percent → fraction as a decimal string without float noise: 12.5 → "0.125". */
export function fraction(percent: number): string {
  return String(Number((percent / 100).toPrecision(12)));
}

type Params = Record<string, string | number | string[]>;

/** What the count above the table counts. Without a name or a data check, a company with none of
 * the shown columns isn't listed (the API's rule), so the count doesn't claim a row of blanks. */
export function countWords(f: ScreenFilters): string {
  return f.q || f.issue ? "" : " with figures in these columns";
}

/** Whether the screener sorts on this key (saved screens and URLs take only these). */
export const sortable = (key: string) => SORTS.has(key);

/** /v1/screener query parameters for a screen, reading these value columns. */
export function screenParams(f: ScreenFilters, columns: string[] = SCREEN_COLUMNS.map((c) => c.key)): Params {
  const where: string[] = [];
  if (f.growth !== undefined) where.push(`revenue_growth_ttm:gte:${fraction(f.growth)}`);
  if (f.margin !== undefined) where.push(`operating_margin_ttm:gte:${fraction(f.margin)}`);
  const p: Params = {
    columns,
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
  if (f.issue) p.issue = f.issue;
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
    f.issue && `data check: ${checkName(f.issue) ?? f.issue}`,
    f.mine && "only companies you can read",
  ].filter(Boolean);
  return parts.length ? parts.join(", ") : "no filters";
}

export const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
  "September", "October", "November", "December"];

/** SEC fiscal year end "0126" (MMDD) → "January". A 52/53-week year ends up to a week into the next
 * month, so a day of 1-7 belongs to the month before: J&J's "0103" is December, Target's "0201"
 * January, and NVIDIA's "0131" stays January. silver.fiscal_year_end_month (migration 042), which the
 * screener's filter and sort use, is the same rule: change both. */
export function fyeMonth(fye: string | null): string | null {
  const m = Number(fye?.slice(0, 2));
  if (!(m >= 1 && m <= 12)) return null;
  const first = Number(fye?.slice(2, 4)) <= 7;
  return MONTHS[first ? (m + 10) % 12 : m - 1]!;
}

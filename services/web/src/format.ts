const SCALES: [number, string][] = [
  [1e12, "T"],
  [1e9, "B"],
  [1e6, "M"],
  [1e3, "K"],
];

/** USD amounts as $96.22B / -$1.30B; per-share values (EPS) as $1.05. */
export function money(value: number, unit: string): string {
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  if (unit !== "USD") return `${sign}$${abs.toFixed(2)}`;
  for (const [size, suffix] of SCALES) {
    if (abs >= size) return `${sign}$${(abs / size).toFixed(2)}${suffix}`;
  }
  return `${sign}$${abs.toFixed(0)}`;
}

/** Ratios as percentages: 0.2134 → "21.3%". */
export function pct(ratio: number, digits = 1): string {
  return `${(ratio * 100).toFixed(digits)}%`;
}

/** Growth with its sign: 0.123 → "+12.3%", -0.04 → "-4.0%". */
export function change(ratio: number): string {
  return `${ratio >= 0 ? "+" : ""}${pct(ratio)}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-10-28" → "Oct 28"; with the year, "Oct 28, 2026". */
export function day(isoDate: string, withYear = false): string {
  const [y, m, d] = isoDate.slice(0, 10).split("-").map(Number);
  return `${MONTHS[m! - 1]} ${d}${withYear ? `, ${y}` : ""}`;
}

/** Quarter label from its end date: "2026-07-26" → "Jul '26". */
export function quarter(periodEnd: string): string {
  const d = new Date(`${periodEnd}T00:00:00Z`);
  const month = d.toLocaleString("en-US", { month: "short", timeZone: "UTC" });
  return `${month} '${String(d.getUTCFullYear()).slice(2)}`;
}

/** "3 min ago", "5 h ago", "2 d ago". */
export function ago(iso: string, now = Date.now()): string {
  const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

// SEC conformed names are upper case with legal suffixes and state tags ("COSTCO WHOLESALE CORP
// /NEW"). Display names drop those and fix casing; CSV exports keep the SEC name.
const NAME_CASE: Record<string, string> = {
  "COCA COLA": "Coca-Cola", JPMORGAN: "JPMorgan", PEPSICO: "PepsiCo", MCDONALDS: "McDonald's",
  "MCDONALD'S": "McDonald's", NVIDIA: "Nvidia", UNITEDHEALTH: "UnitedHealth", "AMAZON COM": "Amazon",
  "EXXON MOBIL": "Exxon Mobil", "KEURIG DR PEPPER": "Keurig Dr Pepper",
};
const SUFFIX = /[\s,]+(&\s*CO|INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|PLC|LLC|L\.?P|N\.?V|S\.?A|AG|SE)\.?$/i;

export function companyName(raw: string): string {
  let s = raw.replace(/[’`]/g, "'").replace(/\s*\/[A-Z]{2,3}\/?\s*$/i, "").replace(/\s*\/NEW\s*$/i, "").trim();
  s = s.replace(/^THE\s+/i, "");
  for (let i = 0; i < 3 && SUFFIX.test(s); i++) s = s.replace(SUFFIX, "").trim();
  s = s.replace(/[,\s]+$/, "");
  const key = s.toUpperCase();
  for (const [k, v] of Object.entries(NAME_CASE)) {
    if (key === k) return v;
    if (key.startsWith(k + " ")) return v + titleCase(s.slice(k.length));
  }
  return /[a-z]/.test(s) ? s : titleCase(s);
}

function titleCase(s: string): string {
  return s.replace(/[A-Za-z][A-Za-z'.]*/g, (w) =>
    /^[A-Z]{1,3}$/.test(w) && !/[AEIOU]/.test(w) ? w : w[0]!.toUpperCase() + w.slice(1).toLowerCase());
}

/** Deep-copies API JSON with display names on every object that has a company_id. */
export function displayNames<T>(data: T): T {
  if (Array.isArray(data)) return data.map(displayNames) as T;
  if (data && typeof data === "object") {
    const o = Object.fromEntries(Object.entries(data).map(([k, v]) => [k, displayNames(v)])) as Record<string, unknown>;
    if (typeof o.company_id === "string" && typeof o.name === "string") o.name = companyName(o.name);
    return o as T;
  }
  return data;
}

/** Operating margin, FCF and net debt don't describe a bank (deposits are its debt, lending its
 * operations), so a missing value there is "n/a", not missing data. */
export function notForBanks(industry: string | null | undefined, metric: string): boolean {
  return !!industry && /\bbank|savings institution|bancorp/i.test(industry)
    && /operating_margin|free_cash_flow|fcf|net_debt/.test(metric);
}

/** What a bank's inapplicable metric shows instead of a dash, and why (its hover). */
export const BANK_NA = "n/a (bank)";
export function bankTitle(label: string): string {
  return `${label} doesn't describe a bank: deposits are its debt and lending is its operations, ` +
    "so the SEC data has no comparable figure.";
}

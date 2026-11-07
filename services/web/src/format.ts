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

/** A filing's "period of report": the period a 10-K or 10-Q covers, but on an 8-K the date of the
 * event it reports, which covers no period. */
export function periodPhrase(form: string | null | undefined, isoDate: string): string {
  return /^8-K/.test(form ?? "") ? `event on ${day(isoDate, true)}` : `period ending ${day(isoDate, true)}`;
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

export { companyName, displayNames, segmentLabel, UNNAMED } from "./displayNames";

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

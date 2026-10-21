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

export const METRIC_LABELS: Record<string, string> = {
  revenue: "Revenue",
  gross_profit: "Gross profit",
  operating_income: "Operating income",
  net_income: "Net income",
  eps_diluted: "EPS (diluted)",
  operating_cash_flow: "Operating cash flow",
  total_assets: "Total assets",
  cash: "Cash",
};

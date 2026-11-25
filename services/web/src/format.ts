const SCALES: [number, string][] = [
  [1e12, "T"],
  [1e9, "B"],
  [1e6, "M"],
  [1e3, "K"],
];

/** Currencies with a symbol of their own; any other reads as its ISO code ("CAD 1.20B"). */
const SYMBOLS: Record<string, string> = { USD: "$", EUR: "€", GBP: "£", JPY: "¥", CNY: "CN¥", INR: "₹", KRW: "₩" };

/** The currency of a unit: "EUR" of "EUR" and "EUR/shares"; USD for anything else (shares, pure). */
export function currencyOf(unit: string | null | undefined): string {
  const code = (unit ?? "").split("/")[0]!;
  return /^[A-Z]{3}$/.test(code) ? code : "USD";
}

const prefix = (code: string) => SYMBOLS[code] ?? `${code} `;

/** Amounts as $96.22B / -$1.30B (€2.10B, CAD 1.20B in another currency, migration 056); per-share
 * values (EPS: a unit like "USD/shares") as $1.05. */
/** Decimals for an amount in its scale: two below 100, one from 100 (Riley 123: "$227.00M" claimed a
 * precision the scale doesn't have; four significant figures at most). The exact figure is in the hover
 * and the CSV. */
const places = (scaled: number) => (Math.abs(scaled) >= 999.95 ? 0 : Math.abs(scaled) >= 99.995 ? 1 : 2);

/** A number with its thousands grouped and a fixed count of decimals: 1743.84 → "1,743.84"
 * (Riley, test 10, finding 85: "$1743.84" in the screener). */
const grouped = (n: number, dp: number) => n.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });

export function money(value: number, unit: string): string {
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  const p = prefix(currencyOf(unit));
  if (!/^[A-Z]{3}$/.test(unit)) return `${sign}${p}${grouped(abs, 2)}`;
  for (const [size, suffix] of SCALES) {
    if (abs >= size) return `${sign}${p}${grouped(abs / size, places(abs / size))}${suffix}`;
  }
  return `${sign}${p}${abs.toFixed(0)}`;
}

/** A figure exactly as a filing states it, every digit grouped: $205,134 and $205,134,000, so two
 * tags of one line read side by side (a data note, migration 075). Per share as $1.0523, shares as
 * "1,234 shares", a ratio as a plain number. Not for tables, which keep four significant figures. */
export function asFiled(value: number, unit: string): string {
  const sign = value < 0 ? "-" : "";
  const digits = Math.abs(value).toLocaleString("en-US", { maximumFractionDigits: 4 });
  if (unit === "shares") return `${sign}${digits} shares`;
  if (/^[A-Z]{3}(\/shares)?$/.test(unit)) return `${sign}${prefix(currencyOf(unit))}${digits}`;
  return `${sign}${digits}`;
}

/** An amount Tributary holds exactly, as a filing's table would print it: "$96,221 million" when it is
 * whole millions, "$12,340 thousand" in thousands, else every digit (asFiled). Never rounded: a source
 * card shows no figure Tributary doesn't hold (.interface-design/system.md §4). */
export function exactAmount(value: number, unit: string): string {
  if (!/^[A-Z]{3}$/.test(unit)) return asFiled(value, unit);
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  const p = prefix(currencyOf(unit));
  if (abs >= 1e6 && abs % 1e6 === 0) return `${sign}${p}${grouped(abs / 1e6, 0)} million`;
  if (abs >= 1e3 && abs % 1e3 === 0) return `${sign}${p}${grouped(abs / 1e3, 0)} thousand`;
  return asFiled(value, unit);
}

const SCALE_WORDS: Record<string, string> = { T: "trillions", B: "billions", M: "millions", K: "thousands" };

/** One unit for a ledger (§4: the unit in the first header cell, "USD billions"): the scale of the
 * largest amount, each figure at two decimals in it ("57.01"). Display only; the exact figure is in
 * the figure's card. */
export function ledgerUnit(values: number[], currency = "USD"): { unit: string; text: (v: number) => string } {
  const top = Math.max(0, ...values.map((v) => Math.abs(v)));
  const scale = SCALES.find(([size]) => top >= size);
  if (!scale) return { unit: currency, text: (v) => grouped(v, 0) };
  return { unit: `${currency} ${SCALE_WORDS[scale[1]]}`, text: (v) => grouped(v / scale[0], 2) };
}

/** A row of amounts in one unit, so periods compare at a glance: the largest value's ($2.43M,
 * $4.10M), or the next smaller one when a value would read under one of the larger and the largest
 * still fits in four digits of the smaller: $119M ... $3,955M, not $0.12B ... $3.96B (Riley, test 10,
 * finding 80), and $55K, $964K, $2,430K rather than a mix of K and M. A value that would round to
 * nothing in that unit keeps its own: a figure never reads as $0.00. `currency`: the row's (USD unless
 * a company reports in another, migration 056). */
export function moneyRow(values: (number | null)[], currency = "USD"): (string | null)[] {
  const sizes = values.flatMap((v) => (v === null || v === 0 ? [] : [Math.abs(v)]));
  const top = Math.max(0, ...sizes);
  let at = SCALES.findIndex(([size]) => top >= size);
  const smaller = at >= 0 ? SCALES[at + 1] : undefined;
  if (smaller && Math.min(...sizes) < SCALES[at]![0] && top / smaller[0] < 9999.5) at += 1;
  const scale = at >= 0 ? SCALES[at] : undefined;
  const p = prefix(currency);
  // one count of decimals for the row, the largest value's, so the row's points line up
  const dp = scale ? places(top / scale[0]) : 2;
  return values.map((v) => {
    if (v === null) return null;
    if (!scale || v === 0 || Math.abs(v) / scale[0] < 0.5 * 10 ** -dp) return money(v, currency);
    return `${v < 0 ? "-" : ""}${p}${grouped(Math.abs(v) / scale[0], dp)}${scale[1]}`;
  });
}

/** How a statement or ledger shows a negative amount: in parentheses, as filings print them
 * ("-$6.05B" → "($6.05B)", "-CAD 1.20B" → "(CAD 1.20B)"). Display only: the amount, the CSV and the
 * hover keep the sign (and F5's `outflow` stays the one place a sign is decided). A percentage or a
 * multiple ("-4.0%", "-0.40x") isn't money and keeps its minus sign; so does anything that isn't a
 * formatted amount. */
export function paren(text: string): string {
  return /^-\D/.test(text) && !/[%x]$/.test(text) ? `(${text.slice(1)})` : text;
}

const WORDS: Record<string, string> = { T: "trillion", B: "billion", M: "million", K: "thousand" };

/** An amount in a sentence: "$302.97B" → "$302.97 billion", "€2.10B" → "€2.10 billion". */
export function inWords(text: string): string {
  return text.replace(/^(.*\d)([TBMK])$/, (_, n: string, s: string) => `${n} ${WORDS[s]}`);
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

/** A moment's date where the viewer is: "2026-11-07T03:00:00Z" is Nov 6 in Phoenix, Nov 7 in
 * London. A timestamp without a zone is local already; one that doesn't parse reads as written. */
export function localDay(timestamp: string, withYear = false): string {
  const at = new Date(timestamp);
  if (Number.isNaN(at.getTime())) return day(timestamp, withYear);
  return `${MONTHS[at.getMonth()]} ${at.getDate()}${withYear ? `, ${at.getFullYear()}` : ""}`;
}

function clockParts(at: Date, zone?: string) {
  const parts = new Intl.DateTimeFormat("en-US", {
    year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", hour12: true,
    timeZoneName: "short", ...(zone ? { timeZone: zone } : {}),
  }).formatToParts(at);
  const get = (t: Intl.DateTimeFormatPartTypes) => parts.find((p) => p.type === t)?.value ?? "";
  return {
    date: (withYear: boolean) => `${get("month")} ${get("day")}${withYear ? `, ${get("year")}` : ""}`,
    // plain spaces: newer ICU puts a narrow no-break space before AM/PM
    clock: `${get("hour")}:${get("minute")} ${get("dayPeriod")} ${get("timeZoneName")}`,
  };
}

/** A moment, written one way everywhere (analyst test 4, #23): the viewer's local date and time
 * with their zone's abbreviation, "Nov 10, 10:11 PM MST" ("Nov 10, 2026, 10:11 PM MST" with the
 * year). Never a UTC clock beside a local one, never a locale's numeric "11/10/2026". A plain date
 * (a period end, a filing date) is `day`; only a moment's date is `localDay`. One that doesn't
 * parse reads as written. `zone` is for tests (an IANA name). */
export function localTime(timestamp: string, withYear = false, zone?: string): string {
  const at = new Date(timestamp);
  if (Number.isNaN(at.getTime())) return timestamp;
  const p = clockParts(at, zone);
  return `${p.date(withYear)}, ${p.clock}`;
}

/** Only the clock of a moment, where the day goes without saying: "10:11 PM MST". */
export function localClock(timestamp: string | Date, zone?: string): string {
  const at = new Date(timestamp);
  return Number.isNaN(at.getTime()) ? String(timestamp) : clockParts(at, zone).clock;
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

/** 43 → "43 s", 108 → "1.8 min", 7200 → "2.0 h". */
export function duration(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 90) return `${Math.round(seconds)} s`;
  if (seconds < 5400) return `${(seconds / 60).toFixed(1)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
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

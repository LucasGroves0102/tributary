/** One line per default, worded from the run's own numbers (the rules are query_api
 * model_maths.py's; nothing is recomputed here): what Reset sets, and why it is defensible. */

import {
  amountUnit,
  builtFromQuarters,
  formatCell,
  LONG_RUN_GROWTH_CAP,
  quartersLabel,
  remainderLabel,
  runBase,
  SCENARIO_SPREAD,
  statLabel,
  type ModelRun,
  type Scenario,
} from "./model";

const pct = (ratio: number | null | undefined) => (ratio === null || ratio === undefined ? "n/a" : `${(ratio * 100).toFixed(1)}%`);
const signed = (ratio: number | null) => (ratio === null ? "n/a" : `${ratio >= 0 ? "+" : ""}${(ratio * 100).toFixed(1)}%`);

/** The lines, in the order of the assumptions' columns. */
export function defaultRules(run: ModelRun, selected: Scenario): { key: string; text: string }[] {
  const rr = run.run_rate;
  const st = run.stats;
  const base = runBase(run);
  const cur = run.current;
  const fromQuarters = builtFromQuarters(run);
  const out: { key: string; text: string }[] = [];
  const ttm = rr.period ? `the trailing twelve months to ${rr.period}` : `FY${base?.fy ?? ""}`;
  // a history outside the long run's bounds says which bound it met, not "held between 0% and 5%" beside 66.9% (Riley, test 10, 65)
  const bound = st.cagr === null ? "" : st.cagr * 100 > LONG_RUN_GROWTH_CAP ? `, capped at ${LONG_RUN_GROWTH_CAP}% for the long run`
    : st.cagr < 0 ? ", floored at 0% for the long run" : "";
  // a CAGR over more than 5 years starts before a trough (model_maths, Statistics: Booking's from FY2019, not FY2020)
  const trough = st.cagr !== null && st.cagr_years > 5 ? ", from the year before a trough" : "";
  const longRun = `${run.long_run.growth.toFixed(1)}% growth (the ${statLabel(st.cagr, st.cagr_years, "CAGR")}${trough}${bound}) and the ${statLabel(st.avg_margin, st.margin_years, "median")} margin`;
  if (fromQuarters && cur?.actual && cur.prior && cur.remainder_prior) {
    const a = cur.actual;
    const r = cur.remainder_prior;
    // the remainder's growth and margin are year 1's run-rate defaults: the TTM's (model_maths)
    out.push({
      key: "ytd",
      text: `FY${cur.fy}E: ${quartersLabel(cur.quarters)} ${cur.quarters === 3 ? "are" : "is"} reported (${formatCell(a.revenue, "USD", amountUnit(run))}, ${signed(a.growth)} on the same quarters a year earlier, at a ${pct(a.margin)} margin). `
        + `${remainderLabel(cur.quarters)} starts from ${remainderLabel(cur.quarters)} FY${cur.fy - 1} (${formatCell(r.revenue, "USD", amountUnit(run))}), grown at ${ttm}'s ${signed(rr.growth)} `
        + `and at its ${pct(rr.margin)} operating margin.`,
    });
    out.push({
      key: "growth",
      text: `From FY${cur.fy + 1}E, growth and operating margin close half the gap each year from ${ttm} (${pct(rr.growth)}, ${pct(rr.margin)}) to the long run: ${longRun}.`,
    });
  } else {
    out.push({
      key: "growth",
      text: `Growth and operating margin start at ${ttm} (${pct(rr.growth)}, ${pct(rr.margin)}) and close half the gap each year to the long run: ${longRun}.`,
    });
  }
  out.push({
    key: "nonOperating",
    // the sign said as what it does to profit, not as a convention to decode (Riley, test 10, finding 65)
    text: `Interest and other: ${ttm}'s operating income less pretax income, ${pct(rr.non_operating ?? 0)} of revenue, every year`
      + `${(rr.non_operating ?? 0) < 0 ? ": a negative cost, so it adds to pretax income" : (rr.non_operating ?? 0) > 0 ? ": a cost, taken off pretax income" : ""}.`,
  });
  const tax = rr.tax_source === "ttm" ? `${ttm}'s effective rate, ${pct(rr.tax_rate)}`
    : rr.tax_source === "five_year" ? `the last five years' tax over pretax income, ${pct(rr.tax_rate)} (the TTM's isn't a usable rate)`
    : `${pct(rr.tax_rate)}, the US federal rate: no usable rate in the filings`;
  out.push({ key: "taxRate", text: `Tax rate: ${tax}, every year.` });
  const shares = rr.share_change === null ? "0%: no comparable diluted share counts in the filings"
    : /Q\d$/.test(rr.share_source ?? "") ? `${signed(rr.share_change)} a year, diluted shares in ${rr.share_source} against the same quarter a year earlier`
    : `${signed(rr.share_change)} a year, the last two 10-Ks' diluted shares`;
  out.push({ key: "shareChange", text: `Share change (buybacks less issuance): ${shares}.` });
  const fcfLong = run.long_run.fcf_margin === null ? "" : `, closing half the gap each year to the ${statLabel(st.avg_fcf_margin, st.fcf_years, "median")}`;
  const fcfYtd = fromQuarters && cur?.remainder_prior?.fcf_margin != null
    ? `; FY${cur.fy}E's ${remainderLabel(cur.quarters)} at ${remainderLabel(cur.quarters)} FY${cur.fy - 1}'s ${pct(cur.remainder_prior.fcf_margin)}`
    : "";
  out.push({
    key: "fcfMargin",
    text: `FCF margin, one rate on revenue (the table shows operating cash flow less capex behind it): ${ttm}'s ${pct(rr.fcf_margin)}${fcfLong}${fcfYtd}.`,
  });
  if (selected !== "base") {
    out.push({
      key: "spread",
      text: `The ${selected} case starts from the base case's defaults, each year's growth ${selected === "bear" ? "down" : "up"} by half (at least ${SCENARIO_SPREAD.growth.min} points) and its margin by a quarter of itself (a positive margin x${selected === "bear" ? 1 - SCENARIO_SPREAD.margin.share : 1 + SCENARIO_SPREAD.margin.share}). `
        + "Its FCF margin follows its own operating margin: the base case's FCF margin that year, moved by the gap between the two cases' operating margins after tax.",
    });
  }
  return out;
}

/** What a figure's source card says (components/SourceCard), built from what the API returns with the
 * figure: its filing (form, filed, accession, EDGAR address), whether it is reported or derived
 * (`value_source`), and for a key stat how it is worked out (data.ts howFiled / statFiled). Words only;
 * nothing is computed here. A card names where the figure sits and opens the filing there: the filing's
 * own printed line needs a cell-to-line lookup (spec §8, product work). */

import type { SourceInfo } from "../components/SourceCard";
import type { MarkKind } from "../components/Mark";
import { day, exactAmount } from "../format";
import { href } from "../route";
import type { FinancialRow, ReleaseCell, ValueSource } from "../types";
import { RELEASE_WORDS, releaseOf, releasePlace } from "./components";
import { statementPlace, statementSection, statFiled, statMark, type KeyStat } from "./data";

const CIK = /^\d{10}$/;
const ACCESSION = /^\d{10}-\d{2}-\d{6}$/;

/** The filing in the reader, at its financial statements; a figure read from a results release (165),
 * at the release itself. */
export function readerAt(companyId: string, src: { accession_no: string; form: string }, release?: ReleaseCell | null): { href: string; label: string } | null {
  if (!CIK.test(companyId) || !ACCESSION.test(src.accession_no)) return null;
  if (release) {
    return {
      href: href({ view: "filing", companyId, accessionNo: src.accession_no, section: release.section }),
      label: `Open the ${src.form} at its press release`,
    };
  }
  return {
    href: href({ view: "filing", companyId, accessionNo: src.accession_no, section: statementSection(src.form) }),
    label: `Open the ${src.form} at its statements`,
  };
}

/** A figure read from a results release's table (165) in its card: no tick (the API says of no such
 * figure that it agrees to the filing, system.md §5.1), its own words instead; how it was read, where
 * in the release it sits, and the reader at the release. Words only: the API's cell, nothing computed. */
export function releaseCard(cell: ReleaseCell, src: { accession_no: string; form: string; filed?: string }, companyId: string):
  Pick<SourceInfo, "mark" | "words" | "how" | "where" | "reader"> {
  return {
    mark: null,
    words: RELEASE_WORDS,
    how: "Read from the release's own table: the company reports this quarter only in its 6-K results release, and no " +
      "XBRL figure covers it, so it carries no tick.",
    where: [`${src.form}${src.filed ? ` filed ${day(src.filed, true)}` : ""}`, releasePlace(cell)],
    reader: companyId ? readerAt(companyId, src, cell) : null,
  };
}

const edgarOf = (url: string | null | undefined) => (url?.startsWith("https://www.sec.gov/") ? url : null);

/** A quarter's (or a year's) figure from /financials: ✓ reported, d derived (italic); none for a figure
 * read from a results release's table (165: its card words it). */
export function rowMark(r: Pick<FinancialRow, "value_source" | "citation">): MarkKind | null {
  if (releaseOf(r)) return null;
  return r.value_source === "derived" ? "d" : "ok";
}

/** A derived figure's rule, in words, from what the API says of it. */
function derivedHow(r: FinancialRow): string {
  if (/^10-K/.test(r.citation.form) && /Q4$/.test(r.fiscal_period ?? "")) {
    return `Derived: the 10-K's fiscal year to ${day(r.period_end, true)} less the three 10-Qs before it. Shown in italic.`;
  }
  // a foreign filer's fiscal Q4 (165): the 20-F's or 40-F's year less the quarters its 6-Ks reported
  const annual = /^(20-F|40-F)/.exec(r.citation.form)?.[1];
  if (annual && /Q4$/.test(r.fiscal_period ?? "")) {
    return `Derived: the ${annual}'s fiscal year to ${day(r.period_end, true)} less its first three quarters. Shown in italic.`;
  }
  return "Derived: worked out from the filings, not printed in one. Shown in italic.";
}

/** A /financials figure's card: "Revenue · three months to Jul 26, 2026", the figure as Tributary holds
 * it, and its filing. `value`: the figure as the table shows it when it isn't the row's own (an outflow
 * shown negative). */
export function rowCard(r: FinancialRow, label: string, companyId: string, span = "three months"): SourceInfo {
  const mark = rowMark(r);
  const n = Number(r.value);
  const title = `${label} · ${span} to ${day(r.period_end, true)}`;
  const value = Number.isFinite(n) ? exactAmount(n, r.unit) : r.value;
  const release = releaseOf(r);
  if (release) return { title, value, ...releaseCard(release, r.citation, companyId), edgar: edgarOf(r.citation.filing_url) };
  return {
    title,
    value,
    mark,
    ...(mark === "d" ? { how: derivedHow(r) } : {}),
    where: [`${r.citation.form} filed ${day(r.citation.filed, true)}`, statementPlace(r.citation.form)],
    reader: readerAt(companyId, r.citation),
    edgar: edgarOf(r.citation.filing_url),
  };
}

/** A key stat's card (the Overview's ledger and hero): its mark from its filing and key, how a figure
 * no filing states is worked out, the filing it is read from. */
export function statCard(s: KeyStat, companyId: string, extra?: string | null): SourceInfo | null {
  if (s.value === null && !s.title) return null;
  const mark = statMark(s);
  const filing = statFiled(s);
  const src: ValueSource | null = s.source;
  const how = filing.filed ? null : `${s.label}, ${filing.how}.`;
  return {
    title: `${s.label} · ${s.period}`,
    value: s.text,
    mark,
    ...(how ? { how } : {}),
    where: src ? [`${src.form} filed ${day(src.filed, true)}`, src.accession_no] : [],
    // a figure that is worked out keeps its full account (TTM EPS lists its quarters); an aside like a finance arm's follows
    ...((() => {
      const note = [mark === "ok" ? null : s.title, extra].filter(Boolean).join(" ");
      return note ? { note } : {};
    })()),
    reader: src && (s.value !== null || s.status === "other_currency") ? readerAt(companyId, src) : null,
  };
}

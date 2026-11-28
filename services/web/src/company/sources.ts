/** What a figure's source card says (components/SourceCard), built from what the API returns with the
 * figure: its filing (form, filed, accession, EDGAR address), whether it is reported or derived
 * (`value_source`), and for a key stat how it is worked out (data.ts howFiled / statFiled). Words only;
 * nothing is computed here. A card names where the figure sits and opens the filing there: at the line
 * that prints it when the API gives one (`citation.line`, migration 168), else at the statements. */

import type { SourceInfo } from "../components/SourceCard";
import type { MarkKind } from "../components/Mark";
import { day, exactAmount } from "../format";
import { href } from "../route";
import type { FinancialRow, PrintedLine, ReleaseCell, ValueSource } from "../types";
import { RELEASE_WORDS, releaseOf, releasePlace } from "./components";
import { statementPlace, statementSection, statFiled, statMark, type KeyStat } from "./data";

const CIK = /^\d{10}$/;
const ACCESSION = /^\d{10}-\d{2}-\d{6}$/;

/** What a card says of a figure whose filing prints another number at its line (168). */
export const PRINTED_OTHERWISE = "The filing prints another number at this figure's line (its inline XBRL ties the two): " +
  "the data and the page disagree, so it carries no tick. Open the line to read it.";

/** A printed line the reader can open: a section and the figure's offsets (168). */
export function lineIn(line: PrintedLine | null | undefined):
  { section: string; at: number; to: number; figure?: "agrees" | "disagrees" } | null {
  if (!line?.section_key || line.char_start === undefined || line.char_end === undefined) return null;
  if (line.char_end <= line.char_start) return null;
  const figure = line.status === "agrees" || line.status === "disagrees" ? line.status : undefined;
  return { section: line.section_key, at: line.char_start, to: line.char_end, ...(figure ? { figure } : {}) };
}

/** The filing in the reader: at the line that prints the figure (168), else at its financial
 * statements; a figure read from a results release (165), at the release itself. */
export function readerAt(companyId: string, src: { accession_no: string; form: string; line?: PrintedLine }, release?: ReleaseCell | null): { href: string; label: string } | null {
  if (!CIK.test(companyId) || !ACCESSION.test(src.accession_no)) return null;
  const printed = release ? null : lineIn(src.line);
  if (printed) {
    return {
      href: href({ view: "filing", companyId, accessionNo: src.accession_no, ...printed }),
      label: `Open the ${src.form} at this line`,
    };
  }
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
  // 168: the filing prints another number at the figure's line: no tick, the card says so
  if (r.citation.line?.status === "disagrees") return null;
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
    ...(r.citation.line?.status === "disagrees" ? { note: PRINTED_OTHERWISE } : {}),
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

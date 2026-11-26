import { onGridKey } from "../components/ledgerGrid";
import { useSourceCard, type SourceInfo } from "../components/SourceCard";
import { TableScroll } from "../components/TableScroll";
import { day, exactAmount, paren } from "../format";
import type { Recap, RecapValue } from "../types";
import { releaseOf } from "./components";
import { recapRows, statementPlace, type RecapDelta, type RecapHead, type RecapRow } from "./data";
import { readerAt, releaseCard } from "./sources";

/** A 10-Q/10-K's quarter against the prior and the year-ago quarter (the recap route), as a ledger
 * (.interface-design/system.md §4): each column names its quarter, its end and the filing it is printed
 * in; every figure opens its card; one tab stop, arrows inside (components/ledgerGrid). n/m readings
 * come from the API (migration 035, `recapRows`), and so does a change's n/m from a zero or negative base
 * (query_api shapes.percent_change). A change keeps its sign, in green or rust for its direction, except
 * a balance's (up is not good or bad by itself: Riley, test 10, finding 20, debt +333% read green).
 * A foreign filer's quarter read from its 6-K results release's table (165) carries no tick: its card
 * says "from the press release" with the table and row, and opens the release (sources.ts releaseCard).
 * `yearAgo`: the quarter and the same quarter a year earlier only (the Earnings view, beside the release). */
export function RecapTable({ data, only, label, yearAgo = false, companyId = "" }: {
  data: Recap; only?: readonly string[]; label: string; yearAgo?: boolean; companyId?: string;
}) {
  const table = recapRows(data, only);
  return (
    <TableScroll label={label}>
      <table className="t led recap" onKeyDown={onGridKey}>
        <thead>
          <tr>
            <th className="first">{label}</th>
            <ColHead head={table.heads[0]} />
            {!yearAgo && <ColHead head={table.heads[1]} />}
            {!yearAgo && <th>QoQ</th>}
            <ColHead head={table.heads[2]} />
            <th>YoY</th>
          </tr>
        </thead>
        <tbody>
          {table.rows.map((r, ri) => (
            <tr key={r.label} data-key={r.metric}>
              <td className="first">{r.label}</td>
              <Figure r={r} col={0} text={paren(r.current)} label={table.heads[0]!.label} companyId={companyId} first={ri === 0} />
              {!yearAgo && <Figure r={r} col={1} text={paren(r.prior)} label={table.heads[1]!.label} companyId={companyId} />}
              {!yearAgo && <Change d={r.qoq} colour={r.colour} />}
              <Figure r={r} col={2} text={paren(r.yearAgo)} label={table.heads[2]!.label} companyId={companyId} />
              <Change d={r.yoy} colour={r.colour} />
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}

function ColHead({ head }: { head: RecapHead }) {
  return (
    <th className={head.derived ? "derived" : undefined}>
      {head.label}
      {head.end && <span className="pe">{day(head.end, true)}</span>}
      {head.source && <span className="src">{head.source}{head.derived && <i> d</i>}</span>}
    </th>
  );
}

/** a change: signed with a true minus, coloured by direction where that means something; or n/m
 * from a zero or negative base with the API's reason in its hover */
function Change({ d, colour }: { d: RecapDelta | null; colour: boolean }) {
  const tone = !d || d.why || !colour ? "" : d.sign > 0 ? " gain" : d.sign < 0 ? " loss" : "";
  return (
    <td className={!d || d.why ? `n faint` : `n${tone}`} title={d?.why ? `Not meaningful: ${d.why}.` : undefined}>
      {d ? d.text.replace(/^-/, "−") : "—"}
    </td>
  );
}

function recapCard(v: RecapValue, label: string, unit: string, period: string, companyId: string): SourceInfo {
  const n = Number(v.value);
  const derived = v.value_source === "derived";
  const title = `${label} · ${period}, the quarter to ${day(v.period_end, true)}`;
  const value = Number.isFinite(n) ? exactAmount(n, unit) : String(v.value);
  const note = v.not_meaningful ? { note: `Not meaningful: ${v.not_meaningful}.` } : {};
  const edgar = v.citation.filing_url?.startsWith("https://www.sec.gov/") ? v.citation.filing_url : null;
  // a figure read from a 6-K results release's table (165): its own words, never the tick
  const release = releaseOf(v);
  if (release) return { title, value, ...releaseCard(release, v.citation, companyId), ...note, edgar };
  return {
    title,
    value,
    mark: v.not_meaningful ? null : derived ? "d" : "ok",
    ...(derived ? { how: "Derived: worked out from the filings, not printed in one. Shown in italic." } : {}),
    ...note,
    where: [`${v.citation.form} filed ${day(v.citation.filed, true)}`, statementPlace(v.citation.form)],
    reader: companyId ? readerAt(companyId, v.citation) : null,
    edgar,
  };
}

function Figure({ r, col, text, label, companyId, first = false }: { r: RecapRow; col: 0 | 1 | 2; text: string; label: string; companyId: string; first?: boolean }) {
  const v = r.values[col];
  const ratio = r.unit === "ratio";
  const { props, card } = useSourceCard(() => (v && !ratio ? recapCard(v, r.label, r.unit, label, companyId) : v ? {
    title: `${r.label} · ${label}, the quarter to ${day(v.period_end, true)}`,
    value: text,
    mark: null,
    how: `A ratio of figures from the ${v.citation.form} filed ${day(v.citation.filed, true)}.`,
    reader: companyId ? readerAt(companyId, v.citation) : null,
  } : null), { hover: false });
  if (!v) return <td className="n faint">{text}</td>;
  return (
    <td className={["n", col === 0 && "current", v.value_source === "derived" && "derived"].filter(Boolean).join(" ")}
      data-cell="" tabIndex={first && col === 0 ? 0 : -1} {...props}>
      {col === 0 ? <b>{text}</b> : text}
      {card}
    </td>
  );
}

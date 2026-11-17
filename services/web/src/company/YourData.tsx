/** The company page's "Your data" (B2c): the user's own datasets that have rows for this company,
 * each with its latest days (query-api /v1/companies/{id}/datasets). Shown only when there are
 * some; an optional panel, so a failure shows nothing rather than taking the page down. The values
 * are the user's own: never a seal, always "yours" in words, the day each was known in its hover. */

import { companyDatasets } from "../api";
import { Folio } from "../components/Folio";
import { TableScroll } from "../components/TableScroll";
import { fieldRef } from "../data/dataset";
import { day, money, pct } from "../format";
import { href } from "../route";
import type { CompanyDatasets, FieldUnit } from "../types";
import { useLoad } from "./useLoad";

/** columns a panel shows; the rest are on Your data */
const MAX_FIELDS = 6;

/** A value as its field's unit reads (exact numbers stay as written). */
export function fieldValue(value: string | null, unit: FieldUnit, currency: string): string {
  if (value === null) return "—";
  const v = Number(value);
  if (!Number.isFinite(v)) return value;
  switch (unit) {
    case "money":
      return money(v, currency);
    case "per_share":
      return money(v, `${currency}/shares`);
    case "ratio":
      return pct(v);
    case "shares":
      return Math.abs(v) >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v.toLocaleString("en-US");
    default:
      return Math.abs(v) >= 1e4 ? v.toLocaleString("en-US", { maximumFractionDigits: 2 }) : String(Number(v.toPrecision(6)));
  }
}

export function YourData({ token, companyId, name }: { token: string; companyId: string; name: string }) {
  const load = useLoad<CompanyDatasets | null>(`${companyId}|yours`, () => companyDatasets(token, companyId));
  const sets = load?.state === "ok" ? load.data?.datasets ?? [] : [];
  if (!sets.length) return null;
  return (
    <Folio name="Your data" line={`Your own figures on ${name}`} aside="Only you can read them" className="your-data">
      {sets.map((d) => {
        const fields = d.fields.slice(0, MAX_FIELDS);
        return (
          <div key={d.dataset_id} className="your-dataset">
            <p className="small">
              <b>{d.title}</b> <span className="faint">mine.{d.name}</span>
              {d.fields.length > fields.length && (
                <span className="faint"> (the first {fields.length} of {d.fields.length} fields; all on <a href={href({ view: "data" })}>Your data</a>)</span>
              )}
            </p>
            <TableScroll label={`${d.title} for ${name}`}>
              <table className="t">
                <thead>
                  <tr>
                    <th className="first">Day</th>
                    {fields.map((f) => <th key={f.name} className="n" title={fieldRef(d.name, f.name)}>{f.name}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {d.rows_here.map((r) => (
                    <tr key={r.day} title={`Known from ${day(r.known_at, true)}`}>
                      <td className="first">{day(r.day, true)}</td>
                      {fields.map((f) => <td key={f.name} className="n">{fieldValue(r.values[f.name] ?? null, f.unit, d.currency)}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScroll>
          </div>
        );
      })}
    </Folio>
  );
}

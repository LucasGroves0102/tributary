/** The Filings tab's insider transactions (#117): every trade in the company's Forms 3, 4 and 5,
 * newest first, with who, their role, what (the SEC's code in words), shares, price, value and the
 * shares owned after, each linking its filing on EDGAR. Holdings (positions, not trades) are left to
 * the filing. The API decides every figure (`company/insiders.ts` words them). */

import { day } from "../format";
import type { Load } from "./useLoad";
import { TableScroll } from "../components/TableScroll";
import type { InsiderFilings } from "../types";
import { ownersText, priceText, sharesText, signedShares, transactions, valueText } from "./insiders";

export function InsiderTable({ load }: { load: Load<InsiderFilings | null> | null }) {
  if (!load || load.state === "loading") return null;
  if (load.state !== "ok") return null; // the filing index below still lists them
  if (load.data === null) {
    return (
      <section className="card">
        <div className="card-h"><h2>Insider transactions</h2></div>
        <p className="empty">Insider transaction details aren't available on this server yet; the filings are listed below.</p>
      </section>
    );
  }
  const rows = transactions(load.data.filings);
  return (
    <section className="card insiders">
      <div className="card-h wrap">
        <h2>Insider transactions</h2>
        <span className="sub">
          Forms 3, 4 and 5 as filed with the SEC · {load.data.filings.length}
          {load.data.has_more ? " most recent" : ""} {load.data.filings.length === 1 ? "filing" : "filings"} · newest trade first
        </span>
      </div>
      {rows.length === 0 ? (
        <p className="empty">
          {load.data.filings.length
            ? "These filings report holdings only, no trades."
            : "No insider filing has been read for this company yet: new ones are read as they're filed."}
        </p>
      ) : (
        <TableScroll label="Insider transactions">
          <table className="t insider-t">
            <thead>
              <tr>
                <th className="first">Insider</th>
                <th>Date</th>
                <th>Transaction</th>
                <th className="n">Shares</th>
                <th className="n">Price</th>
                <th className="n">Value</th>
                <th className="n">Owned after</th>
                <th>Filing</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ filing: f, line: l }) => (
                <tr key={`${f.accession_no}-${l.line}`}>
                  <td className="first">{ownersText(f)}</td>
                  <td className="muted">{l.transaction_date ? day(l.transaction_date, true) : "—"}</td>
                  <td title={l.code ? `Code ${l.code}${l.derivative ? ", a derivative security" : ""}` : undefined}>
                    {l.code_label ?? l.code ?? "—"}
                    {l.derivative && <span className="faint small"> · {l.security_title ?? "derivative"}</span>}
                    {l.direct_indirect === "I" && <span className="faint small"> · {l.nature ?? "indirect"}</span>}
                    {f.aff10b5_one && l.code === "S" && <span className="faint small" title="The filing says the trade was under a Rule 10b5-1 plan"> · 10b5-1 plan</span>}
                  </td>
                  <td className={`n ${l.acquired_disposed === "D" ? "neg" : l.acquired_disposed === "A" ? "pos" : ""}`}>{signedShares(l)}</td>
                  <td className="n">{priceText(l.price)}</td>
                  <td className="n">{valueText(l.value)}</td>
                  <td className="n muted">{sharesText(l.shares_after)}</td>
                  <td>
                    <a href={f.citation.filing_url} target="_blank" rel="noopener noreferrer" title={f.accession_no}>
                      {f.form} {day(f.filed, true)} ↗
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}
      <p className="foot">
        Shares and prices as the filings state them; value = shares × price. A price of $0 is usually an award or a
        gift; a sale under a Rule 10b5-1 plan was scheduled in advance. Holdings without a trade are in each filing on
        EDGAR.
      </p>
    </section>
  );
}

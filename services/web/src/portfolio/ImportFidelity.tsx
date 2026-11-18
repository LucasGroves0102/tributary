/** Import from Fidelity (#/portfolio/import): the user picks Fidelity's Portfolio Positions CSV;
 * the browser reads it and sends its text to the Query API, which reads the positions, drops the
 * account numbers, matches each to a company (or marks it not covered: a fund or ETF) and saves
 * nothing. The preview lists every row with a box to keep or leave it, a cost to type where the
 * export had none, the account label (a name, editable), what's already held, and what was skipped
 * and why. Add saves the chosen rows; the export's Last Price can be kept as the user's own price
 * of the export's day. The file itself is never stored. */

import { useState, type ChangeEvent } from "react";

import { failureReason, importPositions, importPreview } from "../api";
import { Folio } from "../components/Folio";
import { TableScroll } from "../components/TableScroll";
import { toast } from "../components/Toasts";
import { day } from "../format";
import { go, href } from "../route";
import type { ImportPosition, ImportPreview } from "../types";
import { amountOf, cash, exportDateFromName, perShare, shares, SKIP_WORDS, today } from "./portfolio";

interface Row extends ImportPosition {
  keep: boolean;
  cost: string;
  label: string;
}

const MAX_FILE = 512 * 1024;

/** A row the reader left out, in words: "SPAXX (cash or money market)", "pending activity". */
function skipped(s: ImportPreview["skipped"][number]): string {
  if (s.reason === "pending") return SKIP_WORDS.pending;
  return `${s.symbol ?? s.description ?? "a row"} (${SKIP_WORDS[s.reason]})`;
}

/** The file's text, read in the browser (FileReader: every browser, and jsdom). */
function readText(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("unreadable"));
    reader.readAsText(file);
  });
}

export function ImportFidelity({ token }: { token: string }) {
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [priceOn, setPriceOn] = useState(today());
  const [keepPrices, setKeepPrices] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const read = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setError(null);
    if (file.size > MAX_FILE) return setError("That file is over 512 KB: is it Fidelity's positions export?");
    setBusy(true);
    let text: string;
    try {
      text = await readText(file);
    } catch {
      setBusy(false);
      return setError("Couldn't read that file in the browser: try downloading it again.");
    }
    try {
      const p = await importPreview(token, text);
      setPreview(p);
      setPriceOn(p.downloaded ?? exportDateFromName(file.name) ?? today());
      setRows(p.positions.map((x) => ({ ...x, keep: !x.held, cost: x.cost_total ?? "", label: x.account_label ?? "" })));
    } catch (err) {
      setError(`Couldn't read it: ${failureReason(err)}.`);
    } finally {
      setBusy(false);
    }
  };
  const set = (i: number, change: Partial<Row>) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...change } : r)));
  const chosen = rows.filter((r) => r.keep);
  const missing = chosen.filter((r) => amountOf(r.cost, true) === null);

  const save = async () => {
    if (!chosen.length || missing.length) return;
    setBusy(true);
    setError(null);
    try {
      const r = await importPositions(token, {
        positions: chosen.map((x) => ({
          symbol: x.symbol, description: x.description, shares: x.shares, cost_total: amountOf(x.cost, true)!,
          last_price: keepPrices ? x.last_price : null, account_label: x.label.trim() || null,
        })),
        ...(keepPrices && priceOn ? { price_on: priceOn } : {}),
      });
      toast({ text: `Added ${r.lots_added} ${r.lots_added === 1 ? "position" : "positions"} (${r.holdings_added} new ${r.holdings_added === 1 ? "holding" : "holdings"}).` });
      go({ view: "portfolio" });
    } catch (err) {
      setError(`Couldn't add them: ${failureReason(err)}.`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page portfolio-page import-page">
      <div className="row page-bar">
        <h1 className="grow">Import from Fidelity</h1>
        <a className="btn ghost" href={href({ view: "portfolio" })}>Back to the portfolio</a>
      </div>
      <Folio name="The file" lead line="Fidelity's Portfolio Positions export">
        <p className="small import-lead">
          Pick the CSV and check every row before anything is saved. Account numbers are dropped as the file is read: they are never
          stored or shown. The file itself isn't kept.
        </p>
        {/* what it reads, up front (Riley, test 10, finding 52) */}
        <p className="small import-scope">
          It reads Fidelity's export only, for now; for another broker, add each holding by hand from the portfolio.
        </p>
        <details className="how-to-read">
          <summary>Where to find it</summary>
          <p className="small">
            On Fidelity.com: Accounts &amp; Trade, Portfolio, then Positions; choose the accounts, then Download (the arrow above the
            table). The file is called something like Portfolio_Positions_Nov-16-2026.csv. Cash and money-market rows (SPAXX**),
            pending activity and options are left out.
          </p>
        </details>
        <label className="btn import-file">
          {preview ? "Choose another file" : "Choose the CSV"}
          <input type="file" accept=".csv,text/csv" className="visually-hidden" onChange={(e) => void read(e)} disabled={busy} />
        </label>
        {busy && !preview && <p className="faint small" role="status">Reading it…</p>}
        {error && <p className="err small" role="alert">{error}</p>}
      </Folio>

      {preview && (
        <Folio name="Preview" line={`${preview.positions.length} ${preview.positions.length === 1 ? "position" : "positions"} found`}
          aside={preview.downloaded ? `Downloaded ${day(preview.downloaded, true)}` : undefined}>
          {preview.positions.length > 0 ? (
            <TableScroll label="Positions in the export">
              <table className="t import-table">
                <thead>
                  <tr>
                    <th className="first">Add</th>
                    <th>Symbol</th>
                    <th>In Tributary</th>
                    <th className="n">Shares</th>
                    <th className="n">Cost</th>
                    <th className="n">Last price</th>
                    <th>Account</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={r.row} className={r.keep ? undefined : "off"}>
                      <td className="first">
                        <input type="checkbox" checked={r.keep} aria-label={`Add ${r.symbol}`} onChange={(e) => set(i, { keep: e.target.checked })} />
                      </td>
                      <td><b>{r.symbol}</b></td>
                      <td>
                        {r.covered ? (
                          <a href={href({ view: "company", companyId: r.company_id! })} target="_blank" rel="noreferrer">{r.name ?? r.symbol}</a>
                        ) : (
                          <span className="faint" title="Funds, ETFs and anything that isn't a company Tributary reads filings for: kept by symbol and cost, no price">
                            Not covered{r.description ? `: ${r.description}` : ""}
                          </span>
                        )}
                        {r.held && <span className="faint small"> You hold {shares(r.held.shares)} already: adding makes a second buy</span>}
                      </td>
                      <td className="n">{shares(r.shares)}</td>
                      <td className="n">
                        {r.cost_total !== null ? cash(r.cost_total) : (
                          <input className="field num import-cost" inputMode="decimal" aria-label={`Cost of ${r.symbol}`} placeholder="Type it"
                            value={r.cost} onChange={(e) => set(i, { cost: e.target.value })} />
                        )}
                      </td>
                      <td className="n">{r.covered ? perShare(r.last_price) : <span className="faint">—</span>}</td>
                      <td>
                        <input className="field import-label" aria-label={`Account for ${r.symbol}`} value={r.label} maxLength={60}
                          onChange={(e) => set(i, { label: e.target.value })} placeholder="Name" />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScroll>
          ) : <p className="empty">No positions to add: everything in it was skipped.</p>}
          {preview.skipped.length > 0 && (
            <p className="faint small">
              Left out: {preview.skipped.map(skipped).join("; ")}.
            </p>
          )}
          {preview.positions.length > 0 && (
            <div className="pf-form pf-inline import-actions">
              <label className="check">
                <input type="checkbox" checked={keepPrices} onChange={(e) => setKeepPrices(e.target.checked)} />
                Keep the export's last prices as my own, from
              </label>
              <input type="date" className="field" aria-label="The export's day" value={priceOn} max={today()} disabled={!keepPrices}
                onChange={(e) => setPriceOn(e.target.value)} />
              <span className="grow" />
              {missing.length > 0 && <span className="err small">Type a cost for {missing.map((m) => m.symbol).join(", ")}</span>}
              <button className="btn primary" disabled={busy || !chosen.length || missing.length > 0} onClick={() => void save()}>
                Add {chosen.length} {chosen.length === 1 ? "position" : "positions"}
              </button>
            </div>
          )}
        </Folio>
      )}
    </div>
  );
}

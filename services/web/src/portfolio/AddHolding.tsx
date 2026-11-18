/** Add a holding by hand: a company (picked by name or ticker; the API resolves it) or a fund or
 * ETF by its symbol (kept by symbol and cost: not covered), the shares, what was paid (in all or a
 * share), the day, an optional account label (a name, never an account number), and why: the
 * journal's first entry, written with it. Buying a company already held adds a buy to it. */

import { useEffect, useState, type FormEvent } from "react";

import { addHolding, addJournalEntry, failureReason } from "../api";
import { Choices } from "../components/Choices";
import { CompanySearch } from "../components/CompanySearch";
import { amountOf, today } from "./portfolio";

type What = "company" | "fund";

export function AddHolding({ token, onAdded, onCancel }: { token: string; onAdded: (holdingId: string) => void; onCancel: () => void }) {
  const [what, setWhat] = useState<What>("company");
  const [company, setCompany] = useState<{ id: string; name: string } | null>(null);
  const [symbol, setSymbol] = useState("");
  const [fundName, setFundName] = useState("");
  const [count, setCount] = useState("");
  const [costMode, setCostMode] = useState<"total" | "share">("total");
  const [cost, setCost] = useState("");
  const [bought, setBought] = useState(today());
  const [label, setLabel] = useState("");
  const [why, setWhy] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // a problem is about what was sent: any edit clears it (Riley, test 10, finding 16: it stayed on
  // screen after the form was filled in)
  useEffect(() => setError(null), [what, company, symbol, fundName, count, costMode, cost, bought, label, why]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    const n = amountOf(count);
    const c = amountOf(cost, true);
    if (what === "company" && !company) return setError("Pick the company.");
    if (what === "fund" && !/^[A-Za-z0-9][A-Za-z0-9./-]{0,11}$/.test(symbol.trim())) return setError("Type the fund's symbol, such as VOO.");
    if (!n) return setError("Shares: a number above zero.");
    if (c === null) return setError(costMode === "total" ? "What you paid: the buy's total." : "Price per share: what one share cost.");
    setBusy(true);
    try {
      const made = await addHolding(token, {
        ...(what === "company" ? { company_id: company!.id } : { symbol: symbol.trim().toUpperCase(), ...(fundName.trim() ? { name: fundName.trim() } : {}) }),
        lot: {
          shares: n,
          ...(costMode === "total" ? { cost_total: c } : { cost_per_share: c }),
          ...(bought ? { bought_on: bought } : {}),
          ...(label.trim() ? { account_label: label.trim() } : {}),
        },
      });
      if (why.trim()) {
        await addJournalEntry(token, {
          ...(made.company_id ? { company_id: made.company_id } : { symbol: symbol.trim().toUpperCase() }),
          kind: made.created ? "bought" : "added", why: why.trim(), ...(bought ? { decided_on: bought } : {}),
        }).catch(() => undefined); // the buy is saved; the note can be written on its page
      }
      onAdded(made.holding_id);
    } catch (err) {
      setError(`Couldn't add it: ${failureReason(err)}.`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="pf-form card pad" onSubmit={(e) => void submit(e)} aria-labelledby="pf-add-h">
      <div className="row">
        <h2 id="pf-add-h" className="grow">Add a holding</h2>
        <Choices label="What you bought" value={what} set={setWhat} options={[["company", "A company"], ["fund", "A fund or ETF"]]} />
      </div>
      <div className="pf-fields">
        {what === "company" ? (
          <div className="pf-field wide">
            <span>Company</span>
            {company ? (
              <span className="row">
                <b className="grow">{company.name}</b>
                <button type="button" className="btn sm ghost" onClick={() => setCompany(null)}>Change</button>
              </span>
            ) : (
              <CompanySearch token={token} label="Company" placeholder="Name or ticker" autoFocus
                onPick={(id, name, ticker) => setCompany({ id, name: ticker ? `${name} (${ticker})` : name })} />
            )}
          </div>
        ) : (
          <>
            <label className="pf-field">
              <span>Symbol</span>
              <input className="field" value={symbol} onChange={(e) => setSymbol(e.target.value)} placeholder="VOO" maxLength={12} autoFocus />
            </label>
            <label className="pf-field wide">
              <span>Name <span className="faint">(optional)</span></span>
              <input className="field" value={fundName} onChange={(e) => setFundName(e.target.value)} placeholder="Vanguard S&P 500 ETF" maxLength={120} />
            </label>
          </>
        )}
        <label className="pf-field">
          <span>Shares</span>
          <input className="field num" inputMode="decimal" value={count} onChange={(e) => setCount(e.target.value)} placeholder="e.g. 10" />
        </label>
        {/* plain words (Riley, test 10, finding 50: "Cost, in all" and "In all / A share" read as translated) */}
        <div className="pf-field">
          <span>{costMode === "total" ? "What you paid in total" : "Price per share"}</span>
          <span className="row pf-cost">
            <input className="field num" inputMode="decimal" aria-label={costMode === "total" ? "What you paid in total" : "Price per share"} value={cost}
              onChange={(e) => setCost(e.target.value)} placeholder={costMode === "total" ? "e.g. 1,250.00" : "e.g. 125.00"} />
            <Choices label="What you paid, as" value={costMode} set={setCostMode} options={[["total", "Total"], ["share", "Per share"]]} />
          </span>
        </div>
        <label className="pf-field">
          <span>Date bought</span>
          <input type="date" className="field" value={bought} max={today()} onChange={(e) => setBought(e.target.value)} />
        </label>
        <label className="pf-field">
          <span>Account <span className="faint">(optional)</span></span>
          <input className="field" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Brokerage, Roth IRA" maxLength={60}
            title="A name for the account, never its number" />
        </label>
        <label className="pf-field full">
          <span>Why did you buy it? <span className="faint">(the journal's first entry)</span></span>
          <textarea className="field" rows={3} maxLength={4000} value={why} onChange={(e) => setWhy(e.target.value)}
            placeholder="What you expect, and why now" />
        </label>
      </div>
      {error && <p className="err small" role="alert">{error}</p>}
      <div className="row">
        <button className="btn primary" type="submit" disabled={busy}>{busy ? "Adding…" : "Add"}</button>
        <button className="btn ghost" type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

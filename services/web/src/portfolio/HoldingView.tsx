/** One holding (#/portfolio/<id>): its position on the user's own price, the research it hangs on
 * (an Analysis project or a thesis), a review due after a new 10-K/10-Q, its buys and sales, and its
 * journal. Every figure is the API's (query_api/portfolio.py); writes go back to it and the page
 * reloads. Deleting a holding keeps its journal (the API keeps entries by company). */

import { useState, type FormEvent } from "react";

import {
  addLot,
  addSell,
  deleteHolding,
  deleteHoldingPrice,
  deleteLot,
  deleteSell,
  failureReason,
  putHoldingLink,
  putHoldingPrice,
  refreshPortfolioPrices,
} from "../api";
import { useDirectory } from "../companies/directory";
import { Folio } from "../components/Folio";
import { MenuButton } from "../components/MenuButton";
import { TableScroll } from "../components/TableScroll";
import { toast } from "../components/Toasts";
import { day, pct } from "../format";
import { go, href } from "../route";
import type { Holding, JournalKind, Portfolio } from "../types";
import { ConfirmDelete } from "../workspaces/Actions";
import { JournalView } from "./JournalView";
import { NOT_ADVICE } from "./PortfolioPage";
import { amountOf, cash, gainWords, holdingName, perShare, priceWords, reviewWords, shares, signedPct, today } from "./portfolio";

export function HoldingView({ token, holding: h, portfolio, onChanged }: {
  token: string;
  holding: Holding;
  portfolio: Portfolio;
  onChanged: () => void;
}) {
  const directory = useDirectory(token);
  const name = holdingName(h, (id) => directory.label(id));
  const [prompt, setPrompt] = useState<JournalKind | null>(null);
  const [confirm, setConfirm] = useState(false);
  const cur = portfolio.currency;
  const remove = async () => {
    setConfirm(false);
    try {
      await deleteHolding(token, h.id);
      toast({ text: `Removed ${name} from your portfolio. Its journal is kept.` });
      go({ view: "portfolio" });
      onChanged(); // the list is the same page: read it again without the holding
    } catch (e) {
      toast({ text: `Couldn't remove it: ${failureReason(e)}.`, error: true });
    }
  };
  return (
    <div className="page portfolio-page holding-page">
      <div className="row page-bar">
        <h1 className="grow">
          {h.company_id ? <a className="plain" href={href({ view: "company", companyId: h.company_id })}>{name}</a> : name}
        </h1>
        {h.review_due && <button className="btn primary" onClick={() => setPrompt("review")}>Write the review</button>}
        <MenuButton label="More" items={[
          { label: "Back to the portfolio", onSelect: () => go({ view: "portfolio" }) },
          ...(h.company_id ? [{ label: "Open the company", onSelect: () => go({ view: "company", companyId: h.company_id! }) }] : []),
          { label: "Remove from the portfolio", onSelect: () => setConfirm(true) },
        ]} />
      </div>
      {confirm && (
        <ConfirmDelete text={`Remove ${name} from your portfolio?`} yes="Remove"
          detail="Its buys, sales and price go; its journal entries stay, kept by company."
          onYes={() => void remove()} onNo={() => setConfirm(false)} />
      )}

      <Folio name="Position" lead line={h.open ? `${shares(h.shares)} shares` : "Sold in full"}
        aside={h.covered ? undefined : `${h.symbol}: a fund or ETF Tributary doesn't cover, kept by symbol and cost`}>
        {h.review_due && <p className="review-due-note" role="status">{reviewWords(h.review_due)}. Write a review entry to clear it.</p>}
        <div className="ledger holding-ledger">
          <dl>
            <Row k="Shares" v={shares(h.shares)} />
            <Row k="Average cost" v={h.average_cost !== null ? perShare(h.average_cost, cur) : "—"} />
            <Row k="Cost" v={cash(h.cost_basis, cur)} title="What you paid for the shares you hold, at their average cost" />
            <Row k="Realised" v={cash(h.realised, cur)} title="Sales less what the shares sold had cost (average cost; your broker's tax lots may differ)" />
          </dl>
          <dl>
            <Row k="Price" when={h.price ? priceWords(h.price, portfolio.stale_after_days) : undefined}
              v={h.price ? perShare(h.price.value, cur) : "—"} />
            <Row k="Value" v={h.value !== null ? cash(h.value, cur) : "—"} />
            <Row k="Gain" v={h.unrealised !== null ? cash(h.unrealised, cur) : "—"} title={signedPct(h.unrealised_pct) ?? undefined} />
            <Row k="Weight" v={h.weight !== null ? pct(Number(h.weight)) : "—"} />
          </dl>
        </div>
        {h.unrealised !== null && <p className="small">On your price that's {gainWords(h.unrealised, h.unrealised_pct, cur)}.</p>}
        <p className="faint small not-advice">{NOT_ADVICE}</p>
        {h.covered && h.open && <PriceForm token={token} h={h} portfolio={portfolio} onChanged={onChanged} />}
      </Folio>

      {h.covered && (
        <Folio name="Research" line="What this holding hangs on">
          <ResearchLink token={token} h={h} onChanged={onChanged} />
          {h.next_report && (
            <p className="small">
              Next {h.next_report.form}, for the period ending {day(h.next_report.period_end, true)}
              {h.next_report.expected_date ? `, expected around ${day(h.next_report.expected_date, true)}` : ""} (an estimate). A review falls
              due when it lands.
            </p>
          )}
        </Folio>
      )}

      <Folio name="Buys" line={`${h.lots.length} ${h.lots.length === 1 ? "buy" : "buys"}`}>
        <Lots token={token} h={h} cur={cur} onChanged={onChanged} />
      </Folio>

      <Folio name="Sales" line={h.sells.length ? `${h.sells.length} ${h.sells.length === 1 ? "sale" : "sales"}` : "None yet"}>
        <Sells token={token} h={h} cur={cur} onChanged={onChanged} />
      </Folio>

      <Folio name="Journal" line="Why you hold it" aside="Entries are kept as written after 24 hours">
        <JournalView token={token} subject={{ companyId: h.company_id, symbol: h.company_id ? null : h.symbol }}
          reviewDue={!!h.review_due} prompt={prompt}
          monitor={h.link?.kind === "project" ? href({ view: "analysis", projectId: h.link.id, step: "monitor" })
            : h.candidates.find((c) => c.kind === "project") ? href({ view: "analysis", projectId: h.candidates.find((c) => c.kind === "project")!.id, step: "monitor" })
              : undefined}
          onWritten={() => {
            setPrompt(null);
            onChanged();
          }} />
      </Folio>
    </div>
  );
}

function Row({ k, v, when, title }: { k: string; v: string; when?: string; title?: string }) {
  return (
    <div>
      <dt>{k}{when && <span className="when">{when}</span>}</dt>
      <dd className={v === "—" ? "blank" : undefined} title={title}><span className="figure">{v}</span></dd>
    </div>
  );
}

function PriceForm({ token, h, portfolio, onChanged }: { token: string; h: Holding; portfolio: Portfolio; onChanged: () => void }) {
  const [price, setPrice] = useState("");
  const [on, setOn] = useState(today());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async (e: FormEvent) => {
    e.preventDefault();
    const p = amountOf(price);
    if (!p) return setError("A price above zero.");
    setBusy(true);
    setError(null);
    try {
      await putHoldingPrice(token, h.id, p, on);
      setPrice("");
      onChanged();
    } catch (err) {
      setError(`Couldn't save the price: ${failureReason(err)}.`);
    } finally {
      setBusy(false);
    }
  };
  const refresh = async () => {
    setBusy(true);
    try {
      await refreshPortfolioPrices(token);
      onChanged();
    } catch (err) {
      setError(`Couldn't ask your provider: ${failureReason(err)}.`);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="pf-form pf-inline" onSubmit={(e) => void save(e)} aria-label="Your price">
      <label className="pf-field">
        <span>Your price a share</span>
        <input className="field num" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} placeholder="e.g. 100.00" />
      </label>
      <label className="pf-field">
        <span>On</span>
        <input type="date" className="field" value={on} max={today()} onChange={(e) => setOn(e.target.value)} />
      </label>
      <button className="btn" type="submit" disabled={busy}>Save price</button>
      {h.price && h.price.source !== "provider" && (
        <button className="btn ghost" type="button" disabled={busy} onClick={() => void deleteHoldingPrice(token, h.id).then(onChanged, () => undefined)}>
          Clear price
        </button>
      )}
      {portfolio.refresh && <button className="btn ghost" type="button" disabled={busy} onClick={() => void refresh()}>Ask my data provider</button>}
      {error && <p className="err small" role="alert">{error}</p>}
    </form>
  );
}

function ResearchLink({ token, h, onChanged }: { token: string; h: Holding; onChanged: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const value = h.link ? `${h.link.kind}:${h.link.id}` : "";
  const change = async (v: string) => {
    setError(null);
    const [kind, id] = v.split(":");
    try {
      await putHoldingLink(token, h.id, !v ? {} : kind === "project" ? { project_id: id } : { thesis_item_id: id });
      onChanged();
    } catch (err) {
      setError(`Couldn't link it: ${failureReason(err)}.`);
    }
  };
  const options = [...h.candidates, ...(h.link && !h.candidates.some((c) => c.id === h.link!.id) ? [h.link] : [])];
  return (
    <div className="research-link">
      {h.link ? (
        <p>
          Your {h.link.kind === "project" ? "analysis" : "thesis"}:{" "}
          <a href={h.link.kind === "project" ? href({ view: "analysis", projectId: h.link.id }) : href({ view: "thesis", itemId: h.link.id })}>{h.link.name}</a>
        </p>
      ) : options.length ? (
        <p className="small">Link the analysis or thesis this holding rests on, so it's one click away when a report lands.</p>
      ) : (
        <p className="small">
          No analysis of this company yet. <a href={href({ view: "analysis", start: h.company_id! })}>Start one</a>: its thesis and red lines are
          what a review checks.
        </p>
      )}
      {options.length > 0 && (
        <label className="pf-field">
          <span>Linked to</span>
          <select value={value} onChange={(e) => void change(e.target.value)}>
            <option value="">Not linked yet: choose one</option>
            {options.map((c) => (
              <option key={`${c.kind}:${c.id}`} value={`${c.kind}:${c.id}`}>{c.kind === "project" ? "Analysis" : "Thesis"}: {c.name}</option>
            ))}
          </select>
        </label>
      )}
      {error && <p className="err small" role="alert">{error}</p>}
    </div>
  );
}

function Lots({ token, h, cur, onChanged }: { token: string; h: Holding; cur: string; onChanged: () => void }) {
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const remove = async (id: string) => {
    setError(null);
    try {
      await deleteLot(token, id);
      onChanged();
    } catch (err) {
      setError(`Couldn't delete the buy: ${failureReason(err)}.`);
    }
  };
  return (
    <>
      {h.lots.length > 0 && (
        <TableScroll label="Buys">
          <table className="t portfolio-lots">
            <thead>
              <tr>
                <th className="first">Bought</th><th className="n">Shares</th><th className="n">Cost</th><th className="n">A share</th>
                <th>Account</th><th><span className="visually-hidden">Delete</span></th>
              </tr>
            </thead>
            <tbody>
              {h.lots.map((l) => (
                <tr key={l.id}>
                  <td className="first">{l.bought_on ? day(l.bought_on, true) : <span className="faint" title="A positions export has no buy dates">Day not known</span>}
                    {l.source === "fidelity" && <span className="faint small"> (Fidelity export)</span>}</td>
                  <td className="n">{shares(l.shares)}</td>
                  <td className="n">{cash(l.cost_total, cur)}</td>
                  <td className="n">{perShare(l.cost_per_share, cur)}</td>
                  <td>{l.account_label ?? <span className="faint">—</span>}</td>
                  <td><button className="btn sm ghost" aria-label="Delete this buy" onClick={() => void remove(l.id)}>Delete</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}
      {error && <p className="err small" role="alert">{error}</p>}
      {adding ? (
        <TradeForm kind="buy" token={token} h={h} onDone={() => {
          setAdding(false);
          onChanged();
        }} onCancel={() => setAdding(false)} />
      ) : <button className="btn" onClick={() => setAdding(true)}>Add a buy</button>}
    </>
  );
}

function Sells({ token, h, cur, onChanged }: { token: string; h: Holding; cur: string; onChanged: () => void }) {
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const remove = async (id: string) => {
    setError(null);
    try {
      await deleteSell(token, id);
      onChanged();
    } catch (err) {
      setError(`Couldn't delete the sale: ${failureReason(err)}.`);
    }
  };
  return (
    <>
      {h.sells.length > 0 && (
        <TableScroll label="Sales">
          <table className="t portfolio-sells">
            <thead>
              <tr><th className="first">Sold</th><th className="n">Shares</th><th className="n">A share</th><th className="n">Proceeds</th><th><span className="visually-hidden">Delete</span></th></tr>
            </thead>
            <tbody>
              {h.sells.map((s) => (
                <tr key={s.id}>
                  <td className="first">{day(s.sold_on, true)}</td>
                  <td className="n">{shares(s.shares)}</td>
                  <td className="n">{perShare(s.price, cur)}</td>
                  <td className="n">{cash(s.proceeds, cur)}</td>
                  <td><button className="btn sm ghost" aria-label="Delete this sale" onClick={() => void remove(s.id)}>Delete</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}
      {error && <p className="err small" role="alert">{error}</p>}
      {h.open && (adding ? (
        <TradeForm kind="sell" token={token} h={h} onDone={() => {
          setAdding(false);
          onChanged();
        }} onCancel={() => setAdding(false)} />
      ) : <button className="btn" onClick={() => setAdding(true)}>Record a sale</button>)}
    </>
  );
}

function TradeForm({ kind, token, h, onDone, onCancel }: { kind: "buy" | "sell"; token: string; h: Holding; onDone: () => void; onCancel: () => void }) {
  const [count, setCount] = useState("");
  const [price, setPrice] = useState("");
  const [on, setOn] = useState(today());
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const n = amountOf(count);
    const p = amountOf(price, true);
    if (!n) return setError("Shares: a number above zero.");
    if (p === null) return setError("Price a share: a number.");
    setBusy(true);
    setError(null);
    try {
      if (kind === "buy") {
        await addLot(token, h.id, { shares: n, cost_per_share: p, bought_on: on, ...(label.trim() ? { account_label: label.trim() } : {}) });
      } else {
        await addSell(token, h.id, { shares: n, price: p, sold_on: on });
      }
      onDone();
    } catch (err) {
      setError(`Couldn't save it: ${failureReason(err)}.`);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="pf-form pf-inline" onSubmit={(e) => void submit(e)} aria-label={kind === "buy" ? "Add a buy" : "Record a sale"}>
      <label className="pf-field">
        <span>Shares</span>
        <input className="field num" inputMode="decimal" value={count} onChange={(e) => setCount(e.target.value)} autoFocus
          placeholder={kind === "sell" ? shares(h.shares) : "1"} />
      </label>
      <label className="pf-field">
        <span>Price a share</span>
        <input className="field num" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} placeholder="100.00" />
      </label>
      <label className="pf-field">
        <span>{kind === "buy" ? "Bought on" : "Sold on"}</span>
        <input type="date" className="field" value={on} max={today()} onChange={(e) => setOn(e.target.value)} />
      </label>
      {kind === "buy" && (
        <label className="pf-field">
          <span>Account <span className="faint">(optional)</span></span>
          <input className="field" value={label} onChange={(e) => setLabel(e.target.value)} maxLength={60} placeholder="Brokerage" />
        </label>
      )}
      <button className="btn" type="submit" disabled={busy}>{kind === "buy" ? "Add the buy" : "Record the sale"}</button>
      <button className="btn ghost" type="button" onClick={onCancel}>Cancel</button>
      {error && <p className="err small" role="alert">{error}</p>}
    </form>
  );
}

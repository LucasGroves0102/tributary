/** Portfolio (#/portfolio): what the user holds, against the research behind it, valued only on
 * their own prices, with the decision journal that keeps them honest. The Query API decides every
 * figure (query_api/portfolio.py); this screen words them (portfolio.ts). One holding with its buys,
 * sales, price and journal is #/portfolio/<id>; the Fidelity import #/portfolio/import. */

import { useCallback, useEffect, useState } from "react";

import { failureReason, portfolio as fetchPortfolio, putHoldingLink } from "../api";
import { useDirectory } from "../companies/directory";
import { Folio } from "../components/Folio";
import { FormMark } from "../components/FormMark";
import { MenuButton } from "../components/MenuButton";
import { ErrorPanel, Skeleton } from "../components/States";
import { TableScroll } from "../components/TableScroll";
import { toast } from "../components/Toasts";
import { day, pct } from "../format";
import { go, href } from "../route";
import type { Holding, Portfolio } from "../types";
import { AddHolding } from "./AddHolding";
import { HoldingView } from "./HoldingView";
import { ImportFidelity } from "./ImportFidelity";
import { RecentEntries } from "./JournalView";
import { cash, gainWords, holdingName, perShare, priceWords, reviewWords, shares, signedPct, weightTitle } from "./portfolio";

type Load = { state: "loading" } | { state: "ok"; data: Portfolio } | { state: "off" } | { state: "error"; message: string };

/** One load of /v1/portfolio with a reload; null data from an older API (404). */
export function usePortfolio(token: string): { load: Load; reload: () => void } {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    fetchPortfolio(token)
      .then((data) => live && setLoad(data ? { state: "ok", data } : { state: "off" }))
      .catch((e) => live && setLoad({ state: "error", message: failureReason(e) }));
    return () => {
      live = false;
    };
  }, [token, attempt]);
  return { load, reload: useCallback(() => setAttempt((n) => n + 1), []) };
}

export const NOT_ADVICE = "Not investment advice: your own records, on prices you gave. Tributary fetches no prices for you and shares none.";

export function PortfolioPage({ token, holdingId, importing = false }: { token: string; holdingId?: string; importing?: boolean }) {
  // the import reads nothing until a file is picked; leaving it (App keys the page by it) loads afresh
  return importing ? <ImportFidelity token={token} /> : <Screens token={token} holdingId={holdingId} />;
}

function Screens({ token, holdingId }: { token: string; holdingId?: string }) {
  const { load, reload } = usePortfolio(token);
  if (load.state === "loading") return <div className="page"><Skeleton lines={6} label="Loading your portfolio" /></div>;
  if (load.state === "off") {
    return <div className="page"><p className="empty">The portfolio isn't available on this server yet.</p></div>;
  }
  if (load.state === "error") {
    return <div className="page"><ErrorPanel what="your portfolio" detail={load.message} onRetry={reload} /></div>;
  }
  if (holdingId) {
    const h = load.data.holdings.find((x) => x.id === holdingId);
    if (!h) {
      return (
        <div className="page">
          <p className="empty">That holding isn't in your portfolio. <a href={href({ view: "portfolio" })}>Your portfolio</a></p>
        </div>
      );
    }
    return <HoldingView token={token} holding={h} portfolio={load.data} onChanged={reload} />;
  }
  return <Holdings token={token} data={load.data} onChanged={reload} />;
}

function Holdings({ token, data, onChanged }: { token: string; data: Portfolio; onChanged: () => void }) {
  const [adding, setAdding] = useState(false);
  const directory = useDirectory(token);
  const name = (h: Holding) => holdingName(h, (id) => directory.label(id));
  const open = data.holdings.filter((h) => h.open);
  const closed = data.holdings.filter((h) => !h.open);
  const companies = open.filter((h) => h.covered).length;
  const funds = open.length - companies;
  const t = data.totals;
  return (
    <div className="page portfolio-page">
      <div className="row page-bar">
        <h1 className="grow">Portfolio</h1>
        <button className="btn primary" aria-expanded={adding} onClick={() => setAdding((a) => !a)}>Add a holding</button>
        {/* one row on a phone too: the import is the bar's second action, in More (the empty page shows both) */}
        <MenuButton label="More" items={[{ label: "Import from Fidelity", onSelect: () => go({ view: "portfolio", import: true }) }]} />
      </div>
      {adding && (
        <AddHolding token={token} onCancel={() => setAdding(false)} onAdded={() => {
          setAdding(false);
          onChanged();
        }} />
      )}
      {!data.holdings.length ? (
        <Empty />
      ) : (
        <>
          <Folio name="Holdings" lead
            line={`In ${data.currency === "USD" ? "US dollars" : data.currency}, on your own prices`}
            aside={`Cost is the average cost of your buys`}>
            <p className="story portfolio-story">
              You hold{" "}
              {companies > 0 && <b>{companies} {companies === 1 ? "company" : "companies"}</b>}
              {companies > 0 && funds > 0 && " and "}
              {funds > 0 && <b>{funds} {funds === 1 ? "fund" : "funds"}</b>}
              {open.length === 0 && <b>nothing open</b>}, bought for <b>{cash(t.cost_basis)}</b>.
              {t.value !== null && (
                <>
                  {" "}On your own prices {t.priced < open.length ? `${t.priced} of them are` : "they're"} worth <b>{cash(t.value)}</b>
                  {t.unrealised !== null && <>, {gainWords(t.unrealised)}{t.priced < open.length ? " on those" : ""}</>}.
                </>
              )}
              {Number(t.realised) !== 0 && <> Sales so far: {gainWords(t.realised)}.</>}
            </p>
            <p className="faint small not-advice">{NOT_ADVICE}</p>
            {data.reviews_due > 0 && (
              <p className="review-due-note" role="status">
                {data.reviews_due === 1 ? "One holding has" : `${data.reviews_due} holdings have`} a new report since your last review (a 10-K or 10-Q, or results in an 8-K).
              </p>
            )}
            <HoldingsTable token={token} rows={open} data={data} name={name} onChanged={onChanged} />
          </Folio>
          {closed.length > 0 && (
            <Folio name="Closed" line="Sold in full">
              <TableScroll label="Closed holdings">
                <table className="t portfolio-closed">
                  <thead>
                    <tr><th className="first">Holding</th><th className="n">Realised</th></tr>
                  </thead>
                  <tbody>
                    {closed.map((h) => (
                      <tr key={h.id}>
                        <td className="first"><a href={href({ view: "portfolio", holdingId: h.id })}>{name(h)}</a></td>
                        <td className="n">{cash(h.realised)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableScroll>
            </Folio>
          )}
          <Folio name="Journal" line="Your latest entries" aside="Kept as written after 24 hours">
            <RecentEntries token={token} holdings={data.holdings} name={name} label={(id) => directory.label(id)} />
          </Folio>
        </>
      )}
    </div>
  );
}

/** An empty portfolio: one line and one action, how it works folded (Riley, test 10, findings 97 and 106: the
 * heading and Add a holding twice, and 90 words before the first action). Add a holding is the page bar's. */
function Empty() {
  return (
    <div className="portfolio-empty">
      <p className="empty-line">No holdings yet. Track what you own against the research behind it: add one, or import Fidelity's positions file.</p>
      <a className="btn" href={href({ view: "portfolio", import: true })}>Import from Fidelity</a>
      <details className="how-to-read">
        <summary>How the portfolio works</summary>
        <ul className="portfolio-how foot">
          <li><b>Add a holding</b> by hand: a company, the shares, what you paid and when.</li>
          <li><b>Import from Fidelity</b>: its Portfolio Positions CSV. You see every row before anything is saved; account numbers are dropped as the file is read.</li>
          <li><b>Write down why</b>, and what would make you sell. After 24 hours an entry is kept as written; a change is a correction beside it.</li>
          <li><b>Review after each report</b>: when a company you hold files a 10-K or 10-Q, or reports results in an 8-K, a review falls due on the Alerts bell.</li>
        </ul>
        <p className="foot">{NOT_ADVICE} Funds and ETFs are kept by symbol and cost: Tributary covers companies' filings.</p>
      </details>
    </div>
  );
}

/** The Research cell of a holding with nothing linked: a picker of the user's analyses and theses of the
 * company, or a way to start one (Riley, test 10, finding 51: "Link a thesis" only opened the holding). */
function LinkResearch({ token, h, onChanged }: { token: string; h: Holding; onChanged: () => void }) {
  if (!h.candidates.length) {
    return h.company_id ? <a className="faint" href={href({ view: "analysis", start: h.company_id })}>Start an analysis</a> : <span className="faint">—</span>;
  }
  const link = (c: Holding["candidates"][number]) => {
    putHoldingLink(token, h.id, c.kind === "project" ? { project_id: c.id } : { thesis_item_id: c.id })
      .then(() => {
        toast({ text: `Linked ${c.kind === "project" ? "the analysis" : "the thesis"} “${c.name}”.`, topic: `link-${h.id}` });
        onChanged();
      }, (e) => toast({ text: `Couldn't link it: ${failureReason(e)}.`, error: true, topic: `link-${h.id}` }));
  };
  return (
    <MenuButton label="Link…" className="btn sm more-btn" count={false} align="start" title="The analysis or thesis this holding rests on"
      items={h.candidates.map((c) => ({ label: `${c.kind === "project" ? "Analysis" : "Thesis"}: ${c.name}`, onSelect: () => link(c) }))} />
  );
}

function HoldingsTable({ token, rows, data, name, onChanged }: { token: string; rows: Holding[]; data: Portfolio; name: (h: Holding) => string; onChanged: () => void }) {
  if (!rows.length) return <p className="empty">Nothing open: every holding has been sold.</p>;
  return (
    <TableScroll label="Your holdings">
      <table className="t portfolio-table">
        <thead>
          <tr>
            <th className="first">Holding</th>
            <th className="n">Shares</th>
            <th className="n" title="What you paid for the shares you hold (average cost)">Cost</th>
            <th className="n">Price</th>
            <th className="n">Value</th>
            <th className="n" title="Value less cost, on your own price">Gain</th>
            <th className="n" title={weightTitle(data.weight_basis)}>Weight{data.weight_basis === "cost" ? " (cost)" : ""}</th>
            <th>Next report</th>
            <th>Research</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((h) => (
            <tr key={h.id} className={h.review_due ? "review-due" : undefined}>
              <td className="first">
                <a href={href({ view: "portfolio", holdingId: h.id })}>{name(h)}</a>
                {!h.covered && <span className="faint small"> {h.symbol}, not covered</span>}
                {h.review_due && (
                  <span className="holding-review" title={reviewWords(h.review_due)}>Review due</span>
                )}
              </td>
              <td className="n">{shares(h.shares)}</td>
              <td className="n">{cash(h.cost_basis, data.currency)}</td>
              <td className="n" title={h.price ? priceWords(h.price, data.stale_after_days) : h.covered ? "No price yet: type one, or import your Fidelity export" : "Tributary keeps no price for a fund or ETF"}>
                {h.price ? (
                  <>
                    {perShare(h.price.value, data.currency)}
                    <span className={h.price.stale ? "price-src stale" : "price-src"}>{h.price.stale ? "old" : day(h.price.on)}</span>
                  </>
                ) : <span className="faint">—</span>}
              </td>
              <td className="n">{h.value !== null ? cash(h.value, data.currency) : <span className="faint">—</span>}</td>
              <td className="n">
                {h.unrealised !== null ? (
                  <span title={signedPct(h.unrealised_pct) ?? undefined}>{cash(h.unrealised, data.currency)}</span>
                ) : <span className="faint">—</span>}
              </td>
              <td className="n">{h.weight !== null ? pct(Number(h.weight)) : <span className="faint">—</span>}</td>
              <td>
                {h.next_report ? (
                  <span className="next-report" title={`For the period ending ${day(h.next_report.period_end, true)} (an estimate)`}>
                    <FormMark form={h.next_report.form} expected title={null} />
                    {h.next_report.expected_date ? ` ~${day(h.next_report.expected_date)}` : ""}
                  </span>
                ) : <span className="faint">—</span>}
              </td>
              <td>
                {h.link ? (
                  <a href={h.link.kind === "project" ? href({ view: "analysis", projectId: h.link.id }) : href({ view: "thesis", itemId: h.link.id })}>
                    {h.link.name}
                  </a>
                ) : h.covered ? <LinkResearch token={token} h={h} onChanged={onChanged} /> : <span className="faint">—</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}

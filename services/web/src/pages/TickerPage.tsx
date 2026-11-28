import { useEffect, useState } from "react";

import { failureReason, searchCompanies } from "../api";
import { href, type CompanyTab } from "../route";

/** "#/company/NVDA": the company whose current ticker it is, its own address put in place of this one (so
 * Back doesn't come here again), or a page that says no company has it (Marek 16: the ticker address
 * silently showed Home). The search is the palette's (tickers first); only an exact ticker counts. */
export function TickerPage({ token, ticker, tab, replace = (to) => window.location.replace(to) }: {
  token: string;
  ticker: string;
  tab?: CompanyTab;
  /** puts an address in place of this one (tests pass their own) */
  replace?: (to: string) => void;
}) {
  const [state, setState] = useState<"finding" | "none" | string>("finding");
  useEffect(() => {
    let live = true;
    setState("finding");
    searchCompanies(token, ticker, false, 10)
      .then((results) => {
        if (!live) return;
        const hit = results.find((r) => (r.tickers ?? []).some((t) => t.toUpperCase() === ticker));
        if (hit) replace(href({ view: "company", companyId: hit.company_id, ...(tab ? { tab } : {}) }));
        else setState("none");
      })
      .catch((e) => live && setState(failureReason(e)));
    return () => {
      live = false;
    };
    // `replace` is the page's own: a new function each render isn't a new search
  }, [token, ticker, tab]);
  if (state === "finding") {
    return (
      <div className="page" aria-busy="true">
        <p className="empty" role="status">Finding {ticker}…</p>
      </div>
    );
  }
  return (
    <NotFound title={`No company with the ticker ${ticker}`}>
      {state === "none"
        ? <>No company listed here trades as <b>{ticker}</b> today. Find it under <a href={href({ view: "companies" })}>Companies</a> or search with Ctrl K.</>
        : <>Tributary couldn't look {ticker} up: {state}. Find it under <a href={href({ view: "companies" })}>Companies</a> or search with Ctrl K.</>}
    </NotFound>
  );
}

/** The one not-found page (Marek 46: an unknown company had one bare sentence, an unknown page an H1): a
 * heading that says what isn't here and one line on where to go. */
export function NotFound({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="page not-found">
      <h1 tabIndex={-1}>{title}</h1>
      <p className="empty">{children}</p>
    </div>
  );
}

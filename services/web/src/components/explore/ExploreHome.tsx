import { useEffect, useState } from "react";

import { latestFilings, searchCompanies } from "../../api";
import { ago } from "../../format";
import { href } from "../../route";
import type { LatestFiling, Me, SearchResult } from "../../types";

const FEED_REFRESH_MS = 30_000;

export function ExploreHome({ token, me }: { token: string; me: Me | null }) {
  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>Companies</h1>
          <p className="muted">
            Every SEC filer by name, ticker, former name or CIK; values only where your plan
            covers them. The screener (filters by industry, size, growth and margins, with CSV
            export) comes next.
          </p>
        </div>
      </header>
      <Search token={token} />
      <section className="card pad">
        <div className="card-head">
          <h2>Your companies</h2>
        </div>
        {!me ? (
          <p className="muted">Loading…</p>
        ) : me.companies.length === 0 ? (
          <p className="locked">No companies are granted to you.</p>
        ) : (
          <ul className="company-grid">
            {me.companies.map((c) => (
              <li key={c.company_id}>
                <a href={href({ view: "company", companyId: c.company_id })}>
                  <span className="company">{c.name ?? c.company_id}</span>
                  <span className="tiers">
                    {c.tiers.map((t) => (
                      <span key={t} className={`tier ${t}`}>
                        {t}
                      </span>
                    ))}
                  </span>
                </a>
              </li>
            ))}
          </ul>
        )}
      </section>
      <Feed token={token} />
    </div>
  );
}

function Search({ token }: { token: string }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<SearchResult[] | null>(null);

  useEffect(() => {
    const query = q.trim();
    if (!query) {
      setResults(null);
      return;
    }
    let live = true;
    const t = setTimeout(() => {
      searchCompanies(token, query)
        .then((r) => live && setResults(r))
        .catch(() => live && setResults([]));
    }, 250);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [q, token]);

  return (
    <section className="card pad">
      <label className="search">
        <span className="visually-hidden">Search companies</span>
        <input
          type="search"
          value={q}
          placeholder="Search by name, ticker, former name or CIK (e.g. NVDA, Facebook, Google)"
          maxLength={100}
          onChange={(e) => setQ(e.target.value)}
        />
      </label>
      {results && (
        <ul className="results">
          {results.length === 0 && <li className="muted">No matches.</li>}
          {results.map((r) => (
            <li key={r.company_id}>
              <a href={href({ view: "company", companyId: r.company_id })}>
                <span className="company">{r.name}</span>
                <span className="muted small">{matchNote(r)}</span>
                <span className="tiers">
                  {r.access.length ? (
                    r.access.map((t) => (
                      <span key={t} className={`tier ${t}`}>
                        {t}
                      </span>
                    ))
                  ) : (
                    <span className="tier none">no access</span>
                  )}
                </span>
              </a>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Feed({ token }: { token: string }) {
  const [feed, setFeed] = useState<LatestFiling[] | null>(null);

  useEffect(() => {
    let live = true;
    const refresh = () =>
      latestFilings(token)
        .then((r) => live && setFeed(r.filings))
        .catch(() => live && setFeed((f) => f ?? []));
    void refresh();
    const t = setInterval(refresh, FEED_REFRESH_MS);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, [token]);

  return (
    <section className="card pad">
      <div className="card-head">
        <h2>Latest filings</h2>
        <span className="muted small">live · refreshes every 30 s</span>
      </div>
      {feed === null ? (
        <p className="muted">Loading…</p>
      ) : feed.length === 0 ? (
        <p className="locked">
          No filings to show. Filing text needs the pro plan and a grant for the company.
        </p>
      ) : (
        <ul className="filing-list">
          {feed.map((f) => (
            <li key={f.accession_no}>
              <a
                href={href({
                  view: "filing",
                  companyId: f.company_id,
                  accessionNo: f.accession_no,
                  section: null,
                })}
              >
                <span className="form">{f.form}</span>
                <span className="company">{f.name}</span>
                <span>filed {f.filed}</span>
              </a>
              <span className="muted small">ingested {ago(f.ingested_at)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function matchNote(r: SearchResult): string {
  if (!r.is_current) return `formerly ${r.matched_alias}`;
  if (r.matched_on === "ticker") return r.matched_alias;
  return `CIK ${r.company_id}`;
}

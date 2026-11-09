import { useCallback, useEffect, useState } from "react";

import { dataStatus } from "../api";
import { checkName } from "../companies/screen";
import { ago, day, duration } from "../format";
import { href } from "../route";
import type { DataStatus, RecordedRun } from "../types";
import { TableScroll } from "../components/TableScroll";

const REPO = "https://github.com/LucasGroves0102/tributary";

type State = { kind: "loading" } | { kind: "ok"; data: DataStatus } | { kind: "error" };

interface Props {
  load?: () => Promise<DataStatus>;
  now?: () => number;
  /** shown without the app shell (not signed in): adds a way back to the front door */
  signedOut?: boolean;
}

const count = (n: number | null | undefined) => (n === null || n === undefined ? "—" : n.toLocaleString("en-US"));

export { duration };

/** "Nov 6, 2026, 3:30 PM PST": a time means little without its zone, beside "10 pm ET". */
function stamp(iso: string): string {
  const d = new Date(iso);
  const zone = d.toLocaleTimeString("en-US", { timeZoneName: "short" }).split(" ").at(-1);
  return `${d.toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" })} ${zone}`;
}

/** The public status page: how fresh, complete and checked the data is, live from the
 * production database (counts and timestamps only), and the last recorded run of each test
 * suite. Every figure comes from GET /v1/status/data; nothing here is hard-coded. */
export function Status({ load = dataStatus, now = Date.now, signedOut = false }: Props) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const fetchStatus = useCallback(() => {
    let alive = true;
    setState({ kind: "loading" });
    load()
      .then((data) => alive && setState({ kind: "ok", data }))
      .catch(() => alive && setState({ kind: "error" }));
    return () => {
      alive = false;
    };
  }, [load]);
  useEffect(fetchStatus, [fetchStatus]);

  return (
    <div className={signedOut ? "page status-page signed-out" : "page status-page"}>
      {signedOut && (
        <a className="status-home" href={href({ view: "home" })}>
          ← Tributary
        </a>
      )}
      <div className="page-header">
        <h1>Data status</h1>
        <p className="muted">
          How fresh, complete and checked the data is, read live from the production database (counts
          and timestamps only), with the last recorded results of the tests on the assistant and the figures.
          {state.kind === "ok" && (
            <span className="faint">
              {" "}
              As of {stamp(state.data.as_of)}; refreshed at most every {state.data.max_age_seconds} s.
            </span>
          )}
        </p>
      </div>
      {state.kind === "loading" && <p className="muted">Loading the status…</p>}
      {state.kind === "error" && (
        <p className="error" role="alert">
          Couldn't load the status.{" "}
          <button className="btn sm" onClick={fetchStatus}>
            Try again
          </button>
        </p>
      )}
      {state.kind === "ok" && <Body data={state.data} now={now()} signedIn={!signedOut} />}
    </div>
  );
}

function Body({ data, now, signedIn }: { data: DataStatus; now: number; signedIn: boolean }) {
  const { freshness: f, ingest: i, coverage: c } = data;
  const when = (iso: string | null) => (iso ? ago(iso, now) : "—");
  return (
    <>
      <section className="status-strip" aria-label="Freshness">
        <div>
          <div className="k">Newest filing accepted by EDGAR</div>
          <div className="v">{when(f.last_accepted_at)}</div>
          {f.last_accepted_at && <div className="p">{stamp(f.last_accepted_at)}</div>}
        </div>
        <div>
          <div className="k">Newest filing listed here</div>
          <div className="v">{when(f.last_ingested_at)}</div>
          {f.last_ingested_at && <div className="p">{stamp(f.last_ingested_at)}</div>}
        </div>
        <div>
          <div className="k">From EDGAR to here, typically</div>
          <div className="v">{duration(i.p50_seconds)}</div>
          <div className="p">
            95% within {duration(i.p95_seconds)} · {count(i.via_feed)} live-feed filings, last {i.window_days} days
          </div>
        </div>
        <div>
          <div className="k">Caught by the live feed</div>
          <div className="v">{i.live_pct === null ? "—" : `${i.live_pct}%`}</div>
          <div className="p">
            {count(i.via_feed)} of {count(i.filings)} filings; the rest came from EDGAR's daily index
          </div>
        </div>
        <div>
          <div className="k">Companies with quarterly financials</div>
          <div className="v">{count(c.companies_with_financials)}</div>
          <div className="p">from XBRL</div>
        </div>
      </section>
      <p className="faint small status-note">
        EDGAR accepts filings until 10 pm ET, but most accepted after 5:30 pm ET reach its feed the next
        business morning; they count in the latency above.
      </p>
      <div className="status-grid">
        <Completeness data={data} when={when} />
        <section className="card">
          <div className="card-h">
            <h2>Coverage</h2>
          </div>
          <div className="kv">
            <div>
              <div className="k">10-K, 10-Q and 8-K documents stored</div>
              <div className="v">{count(c.documents)}</div>
            </div>
            <div>
              <div className="k">Filings parsed and search-indexed</div>
              <div className="v">{count(c.indexed_filings)}</div>
            </div>
            <div>
              <div className="k">Sections served</div>
              <div className="v">{count(c.sections)}</div>
              <div className="p">search index last updated {when(c.last_indexed_at)}</div>
            </div>
          </div>
        </section>
      </div>
      <Quality data={data} when={when} signedIn={signedIn} />
      <Recorded data={data} />
    </>
  );
}

function Completeness({ data, when }: { data: DataStatus; when: (iso: string | null) => string }) {
  const c = data.completeness;
  return (
    <section className="card">
      <div className="card-h">
        <h2>Completeness against EDGAR</h2>
        <span className="sub">last {c.window_days} calendar days</span>
      </div>
      {c.days_checked === 0 ? (
        <p className="empty">
          <b>First check pending.</b> Each night the pipeline compares the last {c.window_days} days of
          EDGAR's daily master indexes with the database and republishes any filing that's missing; the
          results appear here after its first run.
        </p>
      ) : (
        <>
          <div className="kv">
            <div>
              <div className="k">Filings in EDGAR's daily indexes</div>
              <div className="v">{count(c.indexed)}</div>
              <div className="p">
                {c.days_checked} {c.days_checked === 1 ? "day" : "days"} checked: EDGAR publishes an index only on business days
              </div>
            </div>
            <div>
              <div className="k">Missing when checked</div>
              <div className="v">{count(c.missing)}</div>
              <div className="p">
                {c.days_with_missing ? `all on ${count(c.days_with_missing)} of those days` : "on none of those days"}
              </div>
            </div>
            <div>
              <div className="k">Fetched again</div>
              <div className="v">{count(c.republished)}</div>
              <div className="p">one per filer, and a filing can have several, so this can pass Missing</div>
            </div>
            <div>
              <div className="k">Last check</div>
              <div className="v">{when(c.last_checked_at)}</div>
              {c.last_checked_at && <div className="p">{stamp(c.last_checked_at)}</div>}
            </div>
          </div>
          <p className="foot">
            Each night the last {c.window_days} days of EDGAR's indexes are compared with the database. Missing is what
            each day's latest check found before fetching it again, so a day reads 0 at its next check.
          </p>
        </>
      )}
    </section>
  );
}

/** "12.3%" of the companies checked, or "–" before the first run. */
export function share(part: number, total: number): string {
  if (!total) return "–";
  const pct = (100 * part) / total;
  return `${pct < 0.1 && part > 0 ? "<0.1" : pct.toFixed(1)}%`;
}

/** This page is public and aggregate: never a company here. A signed-in reader follows a row to the
 * screener, which lists the companies they can read with such a finding, under their own access. */
function Quality({ data, when, signedIn }: { data: DataStatus; when: (iso: string | null) => string; signedIn: boolean }) {
  const q = data.quality;
  const withSeries = data.coverage.companies_with_financials;
  // every company with XBRL figures is checked; not all of them have a quarterly series to show
  const noSeries = withSeries !== null && q.companies_checked > withSeries ? q.companies_checked - withSeries : 0;
  return (
    <section className="card">
      <div className="card-h">
        <h2>Data-quality checks</h2>
        <span className="sub">
          {q.last_checked_at
            ? `${count(q.companies_checked)} companies checked · last run ${when(q.last_checked_at)}`
            : "no check has run yet"}
        </span>
      </div>
      {/* #55: say where the companies are; signed out, the page names none (it's public) */}
      <p className="card-note small muted">
        {signedIn ? (
          "Select a count to list those companies in the screener."
        ) : (
          <>
            <a href={href({ view: "home" })}>Sign in</a> to list the companies behind each count; this public page
            shows counts only.
          </>
        )}
      </p>
      <TableScroll label="Data-quality checks">
        <table className="t">
          <thead>
            <tr>
              <th>Check</th>
              <th>Severity</th>
              <th className="n">Companies affected</th>
              <th className="n">Share</th>
              <th className="n">Findings</th>
            </tr>
          </thead>
          <tbody>
            {q.checks.map((c) => (
              <tr key={c.check}>
                <td>{checkName(c.check) ?? c.check}</td>
                <td>
                  <span className={`tag sev-${c.severity}`}>{c.severity}</span>
                </td>
                <td className="n">
                  {signedIn && checkName(c.check) && c.companies ? (
                    <a className="count-link" href={href({ view: "companies", screen: { issue: c.check } })} title="List these companies in the screener">
                      {count(c.companies)}
                    </a>
                  ) : (
                    count(c.companies)
                  )}
                </td>
                <td className="n">{share(c.companies, q.companies_checked)}</td>
                <td className="n">{count(c.findings)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
      <p className="foot">
        A company is checked whenever its figures change, so its own last check can be days old while the latest run
        is minutes old. {noSeries > 0 && `${count(q.companies_checked)} companies have XBRL figures to check; ${count(noSeries)} of them have no quarterly series, which is why ${count(withSeries)} show as having quarterly financials. `}
        A finding is one company, metric and quarter;
        one bad source figure can raise several. SEC data is messy: companies mis-tag figures and restate
        them, and these checks are how we find it. Findings are shown to readers as Data notes on the
        company. Errors and warnings flag figures to double-check (a derived quarter that comes out impossible
        is left out, not shown); restatements are informational.
      </p>
    </section>
  );
}

function source(commit: string, path: string): string {
  return `${REPO}/blob/${commit}/${path}`;
}

function RunLine({ run, path }: { run: RecordedRun; path: string }) {
  return (
    <span className="faint small">
      Recorded {day(run.recorded_at, true)} ·{" "}
      <a href={source(run.commit, path)} target="_blank" rel="noreferrer" title={`The full report on GitHub, as recorded (${run.commit})`}>
        Read the report
      </a>
    </span>
  );
}

function Recorded({ data }: { data: DataStatus }) {
  const { adversarial: a, citations: c, golden: g } = data.recorded;
  return (
    <section className="card">
      <div className="card-h">
        <h2>Tests on the assistant and the figures</h2>
        <span className="sub">last recorded results, not live</span>
      </div>
      <ul className="cover-list">
        <li>
          <b>
            Access: {a.attacks} attempts to reach data a user can't read, {a.leaks} succeeded
          </b>
          <span>
            {a.attacks} prompts in {a.categories} categories tried to get the assistant to reveal data outside
            the user's access; {a.controls_answered} of {a.controls} ordinary questions were still answered with data
            and a citation.
          </span>
          <RunLine run={a} path={a.report} />
        </li>
        <li>
          <b>
            Citations: {c.figures_traced} of {c.figures} figures traced to the data
          </b>
          <span>
            {c.questions} questions; {c.verified_answers} answers fully verified; {c.data_answers_citing} of{" "}
            {c.data_answers} answers using data cite a filing and say how fresh it is; {c.untraced_accessions} citations
            of a filing the assistant hadn't read.
          </span>
          <RunLine run={c} path={c.report} />
        </li>
        <li>
          <b>
            Figures against the filings: {g.figures} numbers printed in {g.filings} 10-Ks
          </b>
          <span>
            The financials pipeline must reproduce what {g.companies} companies printed in their 10-Ks
            {g.checks.absent ? `, with ${g.checks.absent} known gaps asserted absent` : ""}; {g.hand_checked} of the
            figures were also checked by hand against the filings. Re-checked on every code change, and a new version
            ships only after it passes.
          </span>
          <span className="faint small">
            Figures last changed {day(g.committed, true)} ·{" "}
            <a href={source(g.commit, g.readme)} target="_blank" rel="noreferrer" title={`How it's done, on GitHub (${g.commit})`}>
              How it's checked
            </a>
          </span>
        </li>
      </ul>
      <p className="foot">
        The access and citation tests call the AI model, so they aren't re-run live; their recorded answers are
        re-checked automatically on every code change.
      </p>
    </section>
  );
}

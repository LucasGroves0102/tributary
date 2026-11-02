import { useCallback, useEffect, useState } from "react";

import { dataStatus } from "../api";
import { ago, day } from "../format";
import { href } from "../route";
import type { DataStatus, RecordedRun } from "../types";

const REPO = "https://github.com/LucasGroves0102/tributary";

/** What each data-quality check flags (migrations 014, 018, 023: ops.check_financials). */
export const CHECKS: Record<string, string> = {
  impossible_value: "Impossible values: negative revenue, assets or share counts, or a part larger than its total",
  overlapping_quarters: "A quarter starting before the previous one ended",
  calendar_collision: "Two quarters of a metric in one calendar quarter",
  annual_sum: "Four quarters differing from the reported year by more than 1%",
  series_gap: "A quarter missing after four contiguous ones",
  sign_flip: "An expense or outflow whose sign differs from both neighbouring quarters",
  outlier: "A one-quarter spike of 10× or more, typically a scale error",
  segment_sum_business_segment: "Business segments not adding up to the company total (±1%)",
  segment_sum_geography: "Geographic segments not adding up to the company total (±1%)",
  segment_sum_product_service: "Product and service lines not adding up to the company total (±1%)",
  restatement: "A figure a later filing changed by more than 0.5%",
};

type State = { kind: "loading" } | { kind: "ok"; data: DataStatus } | { kind: "error" };

interface Props {
  load?: () => Promise<DataStatus>;
  now?: () => number;
  /** shown without the app shell (not signed in): adds a way back to the front door */
  signedOut?: boolean;
}

const count = (n: number | null | undefined) => (n === null || n === undefined ? "—" : n.toLocaleString("en-US"));

/** 43 → "43 s", 108 → "1.8 min", 7200 → "2.0 h". */
export function duration(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 90) return `${Math.round(seconds)} s`;
  if (seconds < 5400) return `${(seconds / 60).toFixed(1)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}

function stamp(iso: string): string {
  return new Date(iso).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" });
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
          and timestamps only), with the last recorded run of each test suite.
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
      {state.kind === "ok" && <Body data={state.data} now={now()} />}
    </div>
  );
}

function Body({ data, now }: { data: DataStatus; now: number }) {
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
          <div className="k">Newest filing in the database</div>
          <div className="v">{when(f.last_ingested_at)}</div>
          {f.last_ingested_at && <div className="p">{stamp(f.last_ingested_at)}</div>}
        </div>
        <div>
          <div className="k">EDGAR acceptance → database, p50 / p95</div>
          <div className="v">
            {duration(i.p50_seconds)} / {duration(i.p95_seconds)}
          </div>
          <div className="p">
            {count(i.via_feed)} live-feed filings, last {i.window_days} days
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
              <div className="p">last indexed {when(c.last_indexed_at)}</div>
            </div>
          </div>
        </section>
      </div>
      <Quality data={data} when={when} />
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
        <span className="sub">last {c.window_days} days</span>
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
                {c.days_checked} {c.days_checked === 1 ? "day" : "days"} checked
              </div>
            </div>
            <div>
              <div className="k">Missing at the check</div>
              <div className="v">{count(c.missing)}</div>
              <div className="p">
                on {count(c.days_with_missing)} {c.days_with_missing === 1 ? "day" : "days"}
              </div>
            </div>
            <div>
              <div className="k">Republished</div>
              <div className="v">{count(c.republished)}</div>
              <div className="p">one per filer</div>
            </div>
            <div>
              <div className="k">Last check</div>
              <div className="v">{when(c.last_checked_at)}</div>
            </div>
          </div>
          <p className="foot">
            Missing is what each day's latest check found before republishing it, so a day reads 0 at its
            next check.
          </p>
        </>
      )}
    </section>
  );
}

function Quality({ data, when }: { data: DataStatus; when: (iso: string | null) => string }) {
  const q = data.quality;
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
      <div className="tw">
        <table className="t">
          <thead>
            <tr>
              <th>Check</th>
              <th>Severity</th>
              <th className="n">Findings</th>
              <th className="n">Companies</th>
            </tr>
          </thead>
          <tbody>
            {q.checks.map((c) => (
              <tr key={c.check}>
                <td>{CHECKS[c.check] ?? c.check}</td>
                <td>
                  <span className={`tag sev-${c.severity}`}>{c.severity}</span>
                </td>
                <td className="n">{count(c.findings)}</td>
                <td className="n">{count(c.companies)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="foot">
        Run on every company's financials after each refresh. Findings are shown to readers as Data notes
        on the company; errors and warnings flag figures to double-check, restatements are informational.
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
      Recorded {day(run.recorded_at, true)} with {run.models.join(", ")} ·{" "}
      <a href={source(run.commit, path)} target="_blank" rel="noreferrer">
        report at {run.commit}
      </a>
    </span>
  );
}

function Recorded({ data }: { data: DataStatus }) {
  const { adversarial: a, citations: c, golden: g } = data.recorded;
  return (
    <section className="card">
      <div className="card-h">
        <h2>Verification</h2>
        <span className="sub">last recorded runs, not live</span>
      </div>
      <ul className="cover-list">
        <li>
          <b>
            Permission attacks: {a.leaks} {a.leaks === 1 ? "leak" : "leaks"} in {a.attacks}
          </b>
          <span>
            {a.attacks} prompts in {a.categories} categories tried to get the assistant to reveal data outside
            the user's access; {a.controls_answered} of {a.controls} legitimate questions were answered with data
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
            {c.data_answers} answers using data cite a filing and its freshness; {c.untraced_accessions} accession
            numbers not returned by a tool.
          </span>
          <RunLine run={c} path={c.report} />
        </li>
        <li>
          <b>
            Golden figures: {g.figures} figures printed in {g.filings} 10-Ks
          </b>
          <span>
            The financials pipeline must reproduce what {g.companies} companies printed in their 10-Ks
            {g.checks.absent ? `, with ${g.checks.absent} known gaps asserted absent` : ""}; {g.hand_checked} of the
            figures were also checked by hand against the filings. Asserted on every CI run, and images deploy only
            after it passes.
          </span>
          <span className="faint small">
            Figures last changed {day(g.committed, true)} ·{" "}
            <a href={source(g.commit, g.readme)} target="_blank" rel="noreferrer">
              method at {g.commit}
            </a>
          </span>
        </li>
      </ul>
      <p className="foot">
        The attack and citation runs call the model, so they aren't re-run live; CI replays every recorded answer
        and re-checks it on each change.
      </p>
    </section>
  );
}

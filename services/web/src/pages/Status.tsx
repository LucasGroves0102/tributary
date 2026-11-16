import { useCallback, useEffect, useState } from "react";

import { dataStatus } from "../api";
import { checkName } from "../companies/screen";
import { ago, day, duration, localTime } from "../format";
import { href } from "../route";
import type { DataStatus, QualityTier, RecordedRun } from "../types";
import { Seal } from "../components/Seal";
import { TableScroll } from "../components/TableScroll";

const REPO = "https://github.com/LucasGroves0102/tributary";

type State = { kind: "loading" } | { kind: "ok"; data: DataStatus } | { kind: "error" };

interface Props {
  load?: () => Promise<DataStatus>;
  now?: () => number;
  /** shown without the app shell (not signed in): adds a way back to the front door */
  signedOut?: boolean;
  /** signed in: how many companies the reader can read (the sidebar's count), said beside the
   * companies with quarterly financials so the two counts explain each other (#24) */
  readable?: number | null;
}

const count = (n: number | null | undefined) => (n === null || n === undefined ? "—" : n.toLocaleString("en-US"));

export { duration };

/** "Nov 6, 2026, 3:30 PM PST": the app's one way to write a moment (format.ts `localTime`). */
const stamp = (iso: string): string => localTime(iso, true);

/** The public status page: how fresh, complete and checked the data is, live from the
 * production database (counts and timestamps only), and the last recorded run of each test
 * suite. Every figure comes from GET /v1/status/data; nothing here is hard-coded. */
export function Status({ load = dataStatus, now = Date.now, signedOut = false, readable = null }: Props) {
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
        <a className="status-home" href={href({ view: "home" })} aria-label="Tributary, the front door">
          <Seal size="logo" />
          <span className="wordmark">Tributary</span>
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
      {state.kind === "ok" && <Body data={state.data} now={now()} signedIn={!signedOut} readable={signedOut ? null : readable} />}
    </div>
  );
}

function Body({ data, now, signedIn, readable }: { data: DataStatus; now: number; signedIn: boolean; readable: number | null }) {
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
            95% within {duration(i.p95_seconds)}; {count(i.via_feed)} live-feed filings, last {i.window_days} days
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
          <div className="p">{companiesNote(c.companies_with_financials, readable)}</div>
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

/** What the companies count is, beside the other company counts the reader meets (#24): the
 * sidebar's companies you can read (financials of any kind or a stored filing's text) and the
 * checks' companies checked (every company with XBRL figures, below). */
export function companiesNote(withSeries: number | null, readable: number | null): string {
  if (readable === null || withSeries === null || readable <= withSeries) return "from XBRL";
  return `from XBRL; the ${count(readable)} you can read (the sidebar's count) also take in companies with only annual figures or only a filing's text`;
}

/** "12.3%" of the companies checked, or "–" before the first run. */
export function share(part: number, total: number): string {
  if (!total) return "–";
  const pct = (100 * part) / total;
  return `${pct < 0.1 && part > 0 ? "<0.1" : pct.toFixed(1)}%`;
}

/** Findings per 1,000 figures (or documents): "0.10", "13.9", "<0.01" for a real finding, "—" before
 * the checks have counted. */
export function perThousand(n: number | null | undefined, findings = 1): string {
  if (n === null || n === undefined) return "—";
  if (n > 0 && n < 0.01) return "<0.01";
  if (n === 0 && findings > 0) return "<0.01";
  return n >= 10 ? n.toFixed(1) : n.toFixed(2);
}

/** The status page's groups (migration 072's tiers), in order, each with what it means for a reader. */
export const TIERS: { tier: QualityTier; label: string; meaning: string }[] = [
  { tier: "error", label: "Errors readers could see", meaning: "a figure shown that can't be right as it stands" },
  { tier: "warning", label: "To double-check", meaning: "a figure shown that looks wrong and may be" },
  { tier: "kept_out", label: "Kept out", meaning: "source figures the data already leaves out: nothing shown is affected" },
  { tier: "coverage", label: "Coverage", meaning: "a figure missing, not a wrong one: left blank rather than guessed" },
  { tier: "info", label: "Information", meaning: "normal behaviour worth knowing, not an error" },
];

/** What a check's findings mean for someone reading the figures, by check and severity (a check can
 * have findings of two: an outlier on a lumpy line is information). Falls back to the screener's
 * description (companies/screen.ts CHECKS). */
const MEANING: Record<string, string> = {
  impossible_value: "Negative revenue, assets or share counts, or a part larger than its total (gross profit above revenue): one of the figures is tagged wrongly in the filing",
  overlapping_quarters: "A quarter that starts before the previous one ends, usually a change of fiscal calendar: comparisons across it may be off",
  "tag_disagreement:warning": "The filing tags one line two ways that can't both be right (one figure in two scales, a total under a fifth of its own part, two names of a line 5× apart); the figure shown is one of them",
  "statement_mismatch:warning": "The filing's own statement table prints another revenue, net income or total assets than the figure shown, which its XBRL states: one of the two is mistagged, or the table was misread",
  "outlier:warning": "A quarter 10× or more the quarters around it: a one-off, or a scale slip in the filing",
  "outlier:info": "A 10× swing on a line that swings by nature (cash flow subtotals, the change in cash, buybacks, dividends, income tax)",
  "sign_flip:warning": "A cost or outflow with the opposite sign to the quarters around it, usually a sign slip in the filing",
  "sign_flip:info": "Negative stock-based compensation that the filing's own year to date confirms: a benefit, not a slip",
  "annual_sum:warning": "Four quarters more than 1% off the year's reported figure, with no restatement to explain it",
  "annual_sum:info": "Four quarters off a year that a later filing restated (the year or one of its quarters): the restatement explains it",
  calendar_collision: "Two fiscal quarters in one calendar quarter: the calendar view shows one of them",
  segment_sum_business_segment: "Business segments that add up to the company total in no combination (±0.5%)",
  segment_sum_geography: "Geographic areas that add up to the company total in no combination (±0.5%)",
  segment_sum_product_service: "Products and services that add up to the company total in no combination (±0.5%)",
  mistagged_fact: "A source figure the data leaves out: a period ending after its filing, or debt above total liabilities and assets",
  series_gap: "A quarter missing from a series (often capex, buybacks or stock compensation the 10-Qs don't tag): TTM and growth that need it are left blank",
  net_debt_gap: "Debt and cash on file but net debt left out, where investments or debt can't be told apart",
  restatement: "A figure a later filing changed by more than 0.5%: the latest is shown",
};

export function meaning(check: string, severity: string): string {
  return MEANING[`${check}:${severity}`] ?? MEANING[check] ?? checkName(check) ?? check.replace(/_/g, " ");
}

type Check = DataStatus["quality"]["checks"][number];

/** The tier a row belongs to: the API's, else (an older API) its severity. */
export function tierOf(c: Pick<Check, "tier" | "severity">): QualityTier {
  return c.tier ?? c.severity;
}

/** This page is public and aggregate: never a company here. A signed-in reader follows a row to the
 * screener, which lists the companies they can read with such a finding, under their own access. */
function Quality({ data, when, signedIn }: { data: DataStatus; when: (iso: string | null) => string; signedIn: boolean }) {
  const q = data.quality;
  const withSeries = data.coverage.companies_with_financials;
  // every company with XBRL figures is checked; not all of them have a quarterly series to show
  const noSeries = withSeries !== null && q.companies_checked > withSeries ? q.companies_checked - withSeries : 0;
  const groups = TIERS.map((t) => ({ ...t, checks: q.checks.filter((c) => tierOf(c) === t.tier) })).filter((g) => g.checks.length);
  return (
    <section className="card">
      <div className="card-h">
        <h2>Data-quality checks</h2>
        <span className="sub">
          {q.last_checked_at
            ? `${count(q.companies_checked)} companies checked${q.figures ? `, ${count(q.figures)} figures` : ""}, last run ${when(q.last_checked_at)}`
            : "no check has run yet"}
        </span>
      </div>
      {/* #55: say where the companies are; signed out, the page names none (it's public) */}
      <p className="card-note small muted">
        {signedIn ? (
          "Select a count to list those companies in the screener."
        ) : (
          <>
            {/* the front door's sign-in, not the address it already has (Riley 75: it went to #/) */}
            <a href={href({ view: "home" })} onClick={() => window.scrollTo(0, 0)}>Sign in or try the demo</a> to list the companies behind each count; this public page
            shows counts only.
          </>
        )}
      </p>
      <TableScroll label="Data-quality checks">
        <table className="t quality-table">
          <thead>
            <tr>
              <th>Check</th>
              <th className="n">Companies affected</th>
              <th className="n">Share</th>
              <th className="n">Per 1,000 figures</th>
              <th className="n">Findings</th>
            </tr>
          </thead>
          {groups.map((g) => (
            <tbody key={g.tier} className={`tier tier-${g.tier}`}>
              <tr className="tier-h">
                <th colSpan={5} scope="rowgroup">
                  {g.label}: <span className="faint">{g.meaning}</span>
                </th>
              </tr>
              {g.checks.map((c) => (
                <tr key={`${c.check}:${c.severity}`}>
                  <td>{meaning(c.check, c.severity)}</td>
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
                  <td className="n">{perThousand(c.per_1000, c.findings)}</td>
                  <td className="n">{count(c.findings)}</td>
                </tr>
              ))}
            </tbody>
          ))}
        </table>
      </TableScroll>
      {/* what a finding means for what you see first (Riley 48), the rest behind a disclosure */}
      <p className="foot read-first">
        What this means for you: a figure with a finding is marked on its company's Data notes tab, and one that can't be
        right (a derived quarter that comes out impossible) is left out of the tables rather than shown.
      </p>
      <details className="how-to-read">
        <summary>How the checks work</summary>
        <p className="foot">
          A company is checked whenever its figures change, so its own last check can be days old while the latest run
          is minutes old. {noSeries > 0 && `${count(q.companies_checked)} companies have XBRL figures to check; ${count(noSeries)} of them have no quarterly series, which is why ${count(withSeries)} show as having quarterly financials. `}
          A finding is one company, metric and quarter;
          one bad source figure can raise several. Per 1,000 figures counts findings against every quarter, trailing
          twelve months and computed figure of the companies checked. SEC data is messy: companies mis-tag figures and
          restate them, and these checks are how we find it. Only the first two groups are about figures you see that
          may be wrong; the rest say what the data leaves out, what it lacks, and what is normal.
        </p>
      </details>
      {data.parser && <Parser parser={data.parser} when={when} />}
    </section>
  );
}

/** What each parser-output check means for someone reading a filing's sections. */
const PARSER_MEANING: Record<string, string> = {
  invalid_key: "A section labelled with an item its form doesn't have (an 8-K's \"Item 7\", a 10-Q's Part I Item 6)",
  no_sections: "A stored filing that couldn't be split into sections: no item headings were found in its text",
  missing_core_item: "A 10-K without Item 1, 1A, 7 or 8, or a 10-Q without Part I Items 1 and 2",
  duplicate_section: "The same text under two items",
  empty_section: "An item with nothing past its heading",
};

/** The parser-output checks: documents, never a company or filing. */
function Parser({ parser: p, when }: { parser: NonNullable<DataStatus["parser"]>; when: (iso: string | null) => string }) {
  return (
    <>
      <div className="card-h parser-h">
        <h3>Filing sections</h3>
        <span className="sub">
          {p.last_checked_at ? `${count(p.documents_checked)} filings checked, last run ${when(p.last_checked_at)}` : "no check has run yet"}
        </span>
      </div>
      <TableScroll label="Filing section checks">
        <table className="t">
          <thead>
            <tr>
              <th>Check</th>
              <th>Severity</th>
              <th className="n">Filings affected</th>
              <th className="n">Per 1,000 filings</th>
            </tr>
          </thead>
          <tbody>
            {p.checks.map((c) => (
              <tr key={c.check}>
                <td>{PARSER_MEANING[c.check] ?? c.check.replace(/_/g, " ")}</td>
                <td>
                  <span className={`tag sev-${c.severity}`}>{c.severity}</span>
                </td>
                <td className="n">{count(c.documents)}</td>
                <td className="n">{perThousand(c.per_1000, c.documents)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
      <p className="foot">
        Every stored 10-K, 10-Q and 8-K is checked after it is split into items, so a parser rule that files sections
        under the wrong item shows here before readers meet it.
      </p>
    </>
  );
}

function source(commit: string, path: string): string {
  return `${REPO}/blob/${commit}/${path}`;
}

function RunLine({ run, path }: { run: RecordedRun; path: string }) {
  return (
    <span className="faint small run-line">
      <span>Recorded {day(run.recorded_at, true)}</span>
      <a href={source(run.commit, path)} target="_blank" rel="noreferrer" title={`The full report on GitHub, as recorded (${run.commit})`}>
        The report, on GitHub ↗
      </a>
    </span>
  );
}

function Recorded({ data }: { data: DataStatus }) {
  const { adversarial: a, citations: c, golden: g } = data.recorded;
  // printed figures gold doesn't hold, each documented: `absent` (10k_figures.csv) and `differs`
  // (the reference set's exceptions, tests/golden README)
  const gaps = (g.checks.absent ?? 0) + (g.checks.differs ?? 0);
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
            {gaps ? `, with ${gaps} known gaps asserted and explained` : ""}; {g.hand_checked} of the
            figures were also checked by hand against the filings. Re-checked on every code change, and a new version
            ships only after it passes.
          </span>
          <span className="faint small run-line">
            <span>Figures last changed {day(g.committed, true)}</span>
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

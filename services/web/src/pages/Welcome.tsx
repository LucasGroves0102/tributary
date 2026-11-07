import { useEffect, useRef, useState } from "react";

import { accountName, catalogOpen } from "../access";
import {
  ApiError,
  authProviders,
  createSandbox,
  dataStatus,
  GITHUB_SIGN_IN,
  me as fetchMe,
  passwordSignIn,
  sandboxOffer,
} from "../api";
import { duration } from "../format";
import { href } from "../route";
import { saveSession, type Session } from "../session";
import type { DataStatus, Me, SandboxOffer } from "../types";

/** What a refused username/password sign-in says (the API gives every refusal the same 401). */
export const WRONG_PASSWORD = "Wrong username or password, or the account is locked.";

/** The BRD's propagation target: after this, say it's slow and offer to continue. */
const TARGET_MS = 30_000;

type Phase =
  | { kind: "idle" }
  | { kind: "creating"; github: boolean }
  | { kind: "propagating"; visible: number; total: number; elapsedMs: number }
  | { kind: "error"; message: string };

interface Props {
  /** Signed in; `me` is the latest /v1/me (null if it couldn't be read). */
  onReady: (session: Session, me: Me | null) => void;
  /** e.g. "Your demo sandbox expired." */
  notice?: string | null;
  /** Back from GitHub with a token (session.ts `readSignedIn`): finish signing in. */
  github?: { token: string; expiresIn: number } | null;
  pollMs?: number;
  now?: () => number;
}

/** Companies of the sandbox that /v1/me shows with both of a pro plan's tiers (all of them
 * once the public catalog is open to both: then no grant is written or waited for). */
export function readableCount(me: Me, companies: Set<string>): number {
  if (catalogOpen(me, "financials") && catalogOpen(me, "filings")) return companies.size;
  return me.companies.filter(
    (c) =>
      companies.has(c.company_id) && c.tiers.includes("financials") && c.tiers.includes("filings"),
  ).length;
}

/** The public front door: "Try the demo" creates a temporary real user, and "Sign in with
 * GitHub" (when the API has it configured) a permanent one with the same access; either way it
 * then waits, showing progress, until the grants have propagated (app DB → Debezium → Kafka →
 * authz-sync → OpenFGA) and /v1/me lists every company. "Sign in" takes the username and
 * password of an account the owner created (there is no signup) and signs in like an
 * owner-issued sign-in link: session kind "account", no demo scope. */
export function Welcome({ onReady, notice, github, pollMs = 1000, now = Date.now }: Props) {
  const [offer, setOffer] = useState<SandboxOffer | "off" | null>(null);
  const [githubOn, setGithubOn] = useState(false);
  const [passwordOn, setPasswordOn] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [phase, setPhase] = useState<Phase>(() => (github ? { kind: "creating", github: true } : { kind: "idle" }));
  const alive = useRef(true);
  const latest = useRef<{ session: Session; me: Me | null } | null>(null);

  useEffect(() => {
    alive.current = true;
    sandboxOffer()
      .then((o) => alive.current && setOffer(o))
      .catch(() => alive.current && setOffer("off"));
    authProviders()
      .then((p) => {
        if (!alive.current) return;
        setGithubOn(p.github === true);
        setPasswordOn(p.password === true);
      })
      .catch(() => undefined);
    return () => {
      alive.current = false;
    };
  }, []);

  // once, for the token the page was opened with (a ref: StrictMode runs effects twice in dev)
  const finishing = useRef(false);
  useEffect(() => {
    if (!github || finishing.current) return;
    finishing.current = true;
    void finishGithub(github.token, github.expiresIn);
  }, [github]);

  async function finishGithub(token: string, expiresIn: number) {
    let m: Me;
    try {
      m = await fetchMe(token);
    } catch {
      if (alive.current) setPhase({ kind: "error", message: "Couldn't sign in with GitHub. Try again." });
      return;
    }
    if (!alive.current) return;
    if (!m.github) {
      // an owner-issued sign-in link for a regular account (never a sandbox: those start here)
      if (m.sandbox) {
        setPhase({ kind: "error", message: "Couldn't sign in with GitHub. Try again." });
        return;
      }
      const account: Session = {
        kind: "account",
        token,
        userId: m.user_id,
        login: accountName(m),
        expiresAt: new Date(now() + expiresIn * 1000).toISOString(),
        companies: [],
      };
      saveSession(account);
      onReady(account, m);
      return;
    }
    const session: Session = {
      kind: "github",
      token,
      userId: m.user_id,
      login: m.github.login,
      expiresAt: new Date(now() + expiresIn * 1000).toISOString(),
      companies: m.github.company_ids,
    };
    saveSession(session);
    await waitForAccess(session, m);
  }

  /** An owner-created account: the password goes to the API once and is never kept. */
  async function signInWithPassword(username: string, password: string) {
    setPhase({ kind: "creating", github: true });
    let token: string;
    let expiresIn: number;
    let m: Me;
    try {
      ({ token, expires_in: expiresIn } = await passwordSignIn(username, password));
      m = await fetchMe(token);
    } catch (e) {
      if (!alive.current) return;
      const message =
        e instanceof ApiError && e.status === 401
          ? WRONG_PASSWORD
          : e instanceof ApiError && e.status === 429
            ? e.message
            : "Couldn't sign in. Try again.";
      setPhase({ kind: "error", message });
      return;
    }
    if (!alive.current) return;
    const account: Session = {
      kind: "account",
      token,
      userId: m.user_id,
      login: m.account?.username ?? username.trim().toLowerCase(),
      expiresAt: new Date(now() + expiresIn * 1000).toISOString(),
      companies: [],
    };
    saveSession(account);
    onReady(account, m);
  }

  async function start() {
    setPhase({ kind: "creating", github: false });
    let session: Session;
    try {
      const created = await createSandbox();
      session = {
        kind: "sandbox",
        token: created.token,
        userId: created.user_id,
        expiresAt: created.expires_at,
        companies: created.companies.map((c) => c.company_id),
      };
    } catch (e) {
      if (!alive.current) return;
      const message =
        e instanceof ApiError && e.status === 429 ? e.message : "Couldn't start a sandbox. Try again.";
      setPhase({ kind: "error", message });
      return;
    }
    saveSession(session);
    await waitForAccess(session, null);
  }

  /** Polls /v1/me until every company of the session is readable (or "Continue anyway"). */
  async function waitForAccess(session: Session, first: Me | null) {
    latest.current = { session, me: first };
    const ids = new Set(session.companies);
    const started = now();
    let m: Me | null = first;
    while (alive.current) {
      if (!m) {
        try {
          m = await fetchMe(session.token);
        } catch {
          // transient: keep polling
        }
        if (!alive.current) return;
      }
      if (m) latest.current = { session, me: m };
      const visible = m ? readableCount(m, ids) : 0;
      if (m && visible >= ids.size) {
        onReady(session, m);
        return;
      }
      setPhase({ kind: "propagating", visible, total: ids.size, elapsedMs: now() - started });
      await new Promise((r) => setTimeout(r, pollMs));
      m = null;
    }
  }

  function continueAnyway() {
    const l = latest.current;
    if (!l) return;
    alive.current = false; // stop polling; the app shows companies as access arrives
    onReady(l.session, l.me);
  }

  const busy = phase.kind === "creating" || phase.kind === "propagating";
  const o = offer && offer !== "off" ? offer : null;
  // what its plan reads, instead of a plan's name a visitor has no way to know
  const reads = o?.plan === "pro" ? "financials and filing text" : "financials";
  return (
    <main className="welcome">
      <div className="welcome-layout">
        <header className="welcome-head">
          <h1 id="welcome-title" className="welcome-brand">
            <img src="/favicon.svg" alt="" width={22} height={22} />
            Tributary
          </h1>
          <p className="welcome-lead">
            SEC filings and XBRL financials as they're filed, with an assistant that cites the filing
            behind every number.
          </p>
        </header>
        <section className="card pad welcome-card" aria-labelledby="welcome-title">
          {notice && (
            <p className="welcome-notice" role="status">
              {notice}
            </p>
          )}
          <div className="welcome-try">
            {/* one primary button at a time: the form's Sign in takes over while it's open */}
            <button
              className={formOpen ? "btn" : "btn primary"}
              disabled={busy || offer === "off" || offer === null}
              onClick={() => void start()}
            >
              Try the demo
            </button>
            {offer === "off" ? (
              <p className="small muted">The demo isn't available right now.</p>
            ) : (
              o && (
                <p className="small faint">
                  Starts a private sandbox for {o.ttl_hours} hours with access to{" "}
                  {o.catalog_public ? `every company's ${reads}` : `the ${reads} of ${o.tickers.length} companies (${o.tickers.join(", ")})`}
                  {o.agent
                    ? `, and ${o.agent_questions_per_day} assistant questions a day`
                    : "; the assistant is off for demo sandboxes"}
                  . No sign-up. It's deleted when it expires.
                </p>
              )
            )}
            <Progress phase={phase} onContinue={continueAnyway} />
          </div>
          {(githubOn || passwordOn) && (
            <div className="welcome-account">
              <div className="row wrap">
                {githubOn && !busy && (
                  <a className="btn" href={GITHUB_SIGN_IN}>
                    Sign in with GitHub
                  </a>
                )}
                {/* stays where it is: it opens the form below, then closes it */}
                {passwordOn && !busy && (
                  <button className="btn" aria-expanded={formOpen} onClick={() => setFormOpen((f) => !f)}>
                    {formOpen ? "Cancel" : "Sign in"}
                  </button>
                )}
              </div>
              {githubOn && (
                <p className="small faint">
                  Signing in with GitHub gives you the same {o?.catalog_public ? "access" : "companies"} in an account that stays, with your
                  saved work. It reads only your public GitHub profile.
                </p>
              )}
              {passwordOn && formOpen && (
                <PasswordForm busy={busy} onSubmit={(u, p) => void signInWithPassword(u, p)} />
              )}
            </div>
          )}
        </section>
        <section className="welcome-proof" aria-label="What's in Tributary">
          <Proof />
          <ul className="welcome-list">
            <li>Screen every company on revenue, growth, margins and debt, and save the screen.</li>
            <li>Read 10-K, 10-Q and 8-K filings by section, and see what changed since the last one.</li>
            <li>Chart any metric or your own formula; every point opens the filing behind it.</li>
            <li>Compare a company with its peers, then build a guided analysis with a thesis and a model.</li>
            <li>Ask the assistant: it answers from the filings and cites them. No stock prices, consensus estimates or call transcripts.</li>
          </ul>
          <p className="small faint">
            <a href={href({ view: "status" })}>Data status</a>: how fresh, complete and checked the data is,
            live.
          </p>
        </section>
      </div>
    </main>
  );
}

/** The figures beside the sign-in: the public status route's own, so they can't drift from it. None
 * is hard-coded; a figure the API hasn't got is left out. */
export function proofFigures(d: DataStatus): { value: string; what: string }[] {
  const n = (v: number) => v.toLocaleString("en-US");
  const { coverage: c, ingest: i, recorded: r } = d;
  return [
    c.companies_with_financials !== null && { value: n(c.companies_with_financials), what: "companies with quarterly financials from XBRL" },
    c.documents !== null && { value: n(c.documents), what: "10-K, 10-Q and 8-K documents stored" },
    i.p50_seconds !== null && {
      value: duration(i.p50_seconds),
      what: `typically from EDGAR to here${i.p95_seconds !== null ? `; 95% within ${duration(i.p95_seconds)}` : ""}`,
    },
    r.citations.figures > 0 && { value: `${n(r.citations.figures_traced)} of ${n(r.citations.figures)}`, what: "figures in the assistant's test answers traced to the data" },
    r.adversarial.attacks > 0 && { value: `${n(r.adversarial.leaks)} of ${n(r.adversarial.attacks)}`, what: "attempts to reach data a user can't read succeeded in testing" },
  ].filter((f): f is { value: string; what: string } => !!f);
}

function Proof() {
  const [data, setData] = useState<DataStatus | null>(null);
  useEffect(() => {
    let live = true;
    dataStatus().then((d) => live && setData(d)).catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);
  // the page reads the same with these or without them; an old or partial answer shows none
  const figures = (() => {
    try {
      return data ? proofFigures(data) : [];
    } catch {
      return [];
    }
  })();
  if (!figures.length) return null;
  return (
    <dl className="proof">
      {figures.map((f) => (
        <div key={f.what}>
          <dt>{f.value}</dt>
          <dd>{f.what}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Username and password of an owner-created account. The password lives only in this form's
 * state until it is sent, and is cleared as soon as it is. */
function PasswordForm({
  busy,
  onSubmit,
}: {
  busy: boolean;
  onSubmit: (username: string, password: string) => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  return (
    <form
      className="welcome-signin"
      aria-label="Sign in with a username and password"
      onSubmit={(e) => {
        e.preventDefault();
        if (busy || !username.trim() || !password) return;
        const p = password;
        setPassword("");
        onSubmit(username, p);
      }}
    >
      <label htmlFor="signin-username">Username</label>
      <input
        id="signin-username"
        className="field"
        name="username"
        autoComplete="username"
        autoCapitalize="none"
        spellCheck={false}
        required
        maxLength={32}
        value={username}
        onChange={(e) => setUsername(e.target.value)}
      />
      <label htmlFor="signin-password">Password</label>
      <input
        id="signin-password"
        className="field"
        type="password"
        name="password"
        autoComplete="current-password"
        required
        maxLength={256}
        value={password}
        onChange={(e) => setPassword(e.target.value)}
      />
      <div className="row">
        <button className="btn primary" type="submit" disabled={busy}>
          Sign in
        </button>
      </div>
      <p className="small faint">For accounts the owner created. There is no sign-up.</p>
    </form>
  );
}

function Progress({ phase, onContinue }: { phase: Phase; onContinue: () => void }) {
  if (phase.kind === "idle") return null;
  if (phase.kind === "error") {
    return (
      <p className="welcome-error" role="alert">
        {phase.message}
      </p>
    );
  }
  if (phase.kind === "creating") {
    return (
      <p className="small" role="status">
        {phase.github ? "Signing you in…" : "Creating your user…"}
      </p>
    );
  }
  const seconds = Math.round(phase.elapsedMs / 1000);
  const slow = phase.elapsedMs >= TARGET_MS;
  return (
    <div className="welcome-progress" role="status">
      <div className="row">
        <span className="grow">
          Granting access: <b>{phase.visible}</b> of {phase.total} companies readable
        </span>
        <span className="faint num">{seconds} s</span>
      </div>
      <progress value={phase.visible} max={phase.total} aria-label="Companies readable" />
      <p className="small faint">
        Your account is created. Permission to read each company is on its way through the system, usually in
        under 30 s.
      </p>
      {slow && (
        <p className="small">
          This is taking longer than usual.{" "}
          <button className="btn sm" onClick={onContinue}>
            Continue anyway
          </button>{" "}
          Companies appear as their access arrives.
        </p>
      )}
    </div>
  );
}

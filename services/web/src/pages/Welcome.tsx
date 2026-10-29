import { useEffect, useRef, useState } from "react";

import { ApiError, authProviders, createSandbox, GITHUB_SIGN_IN, me as fetchMe, sandboxOffer } from "../api";
import { saveSession, type Session } from "../session";
import type { Me, SandboxOffer } from "../types";

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

/** Companies of the sandbox that /v1/me shows with both of a pro plan's tiers. */
export function readableCount(me: Me, companies: Set<string>): number {
  return me.companies.filter(
    (c) =>
      companies.has(c.company_id) && c.tiers.includes("financials") && c.tiers.includes("filings"),
  ).length;
}

/** The public front door: "Try the demo" creates a temporary real user, and "Sign in with
 * GitHub" (when the API has it configured) a permanent one with the same access; either way it
 * then waits, showing progress, until the grants have propagated (app DB → Debezium → Kafka →
 * authz-sync → OpenFGA) and /v1/me lists every company. */
export function Welcome({ onReady, notice, github, pollMs = 1000, now = Date.now }: Props) {
  const [offer, setOffer] = useState<SandboxOffer | "off" | null>(null);
  const [githubOn, setGithubOn] = useState(false);
  const [phase, setPhase] = useState<Phase>(() => (github ? { kind: "creating", github: true } : { kind: "idle" }));
  const alive = useRef(true);
  const latest = useRef<{ session: Session; me: Me | null } | null>(null);

  useEffect(() => {
    alive.current = true;
    sandboxOffer()
      .then((o) => alive.current && setOffer(o))
      .catch(() => alive.current && setOffer("off"));
    authProviders()
      .then((p) => alive.current && setGithubOn(p.github === true))
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
      setPhase({ kind: "error", message: "Couldn't sign in with GitHub. Try again." });
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
  return (
    <main className="welcome">
      <section className="card pad welcome-card" aria-labelledby="welcome-title">
        <h1 id="welcome-title">Tributary</h1>
        <p className="muted">
          SEC filings and XBRL financials as they're filed, with an assistant that cites the filing
          behind every number.
        </p>
        {notice && (
          <p className="welcome-notice" role="status">
            {notice}
          </p>
        )}
        <div className="row">
          <button
            className="btn primary"
            disabled={busy || offer === "off" || offer === null}
            onClick={() => void start()}
          >
            Try the demo
          </button>
          {githubOn && !busy && (
            <a className="btn" href={GITHUB_SIGN_IN}>
              Sign in with GitHub
            </a>
          )}
        </div>
        {offer === "off" ? (
          <p className="small muted">The demo isn't available right now.</p>
        ) : (
          o && (
            <p className="small faint">
              Starts a private sandbox for {o.ttl_hours} hours: a {o.plan} account with access to{" "}
              {o.tickers.length} companies ({o.tickers.join(", ")})
              {o.agent
                ? ` and ${o.agent_questions_per_day} assistant questions a day`
                : "; the assistant is off for demo sandboxes"}
              . No sign-up. It's deleted when it expires.
            </p>
          )
        )}
        {githubOn && (
          <p className="small faint">
            Signing in with GitHub gives you the same companies in an account that stays, with your
            saved work. It reads only your public GitHub profile.
          </p>
        )}
        <Progress phase={phase} onContinue={continueAnyway} />
      </section>
    </main>
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
        Your user and its grants are in the app database. Access reaches the Query API through
        Debezium, Kafka and authz-sync into OpenFGA and Postgres row-level security, usually in
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

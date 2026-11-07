import { useEffect, useRef, useState } from "react";

import { accountName, catalogOpen } from "../access";
import {
  ApiError,
  authProviders,
  createSandbox,
  GITHUB_SIGN_IN,
  me as fetchMe,
  passwordSignIn,
  sandboxOffer,
} from "../api";
import { href } from "../route";
import { saveSession, type Session } from "../session";
import type { Me, SandboxOffer } from "../types";

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
          {passwordOn && !busy && !formOpen && (
            <button className="btn" onClick={() => setFormOpen(true)}>
              Sign in
            </button>
          )}
        </div>
        {passwordOn && formOpen && (
          <PasswordForm
            busy={busy}
            onSubmit={(u, p) => void signInWithPassword(u, p)}
            onCancel={() => setFormOpen(false)}
          />
        )}
        {offer === "off" ? (
          <p className="small muted">The demo isn't available right now.</p>
        ) : (
          o && (
            <p className="small faint">
              Starts a private sandbox for {o.ttl_hours} hours with access to {o.catalog_public ? "every company" : `${o.tickers.length} companies (${o.tickers.join(", ")})`}:{" "}
              {o.plan === "pro" ? "financials and filing text" : "financials"}
              {o.agent
                ? ` and ${o.agent_questions_per_day} assistant questions a day`
                : "; the assistant is off for demo sandboxes"}
              . No sign-up. It's deleted when it expires.
            </p>
          )
        )}
        {githubOn && (
          <p className="small faint">
            Signing in with GitHub gives you the same {o?.catalog_public ? "access" : "companies"} in an account that stays, with your
            saved work. It reads only your public GitHub profile.
          </p>
        )}
        <Progress phase={phase} onContinue={continueAnyway} />
        <p className="small faint">
          <a href={href({ view: "status" })}>Data status</a>: how fresh, complete and checked the data is,
          live.
        </p>
      </section>
    </main>
  );
}

/** Username and password of an owner-created account. The password lives only in this form's
 * state until it is sent, and is cleared as soon as it is. */
function PasswordForm({
  busy,
  onSubmit,
  onCancel,
}: {
  busy: boolean;
  onSubmit: (username: string, password: string) => void;
  onCancel: () => void;
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
        <button className="btn" type="button" onClick={onCancel} disabled={busy}>
          Cancel
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

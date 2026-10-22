import { useEffect, useRef, useState } from "react";

import { ApiError, createSandbox, me as fetchMe, sandboxOffer } from "../api";
import { saveSession, type Session } from "../session";
import type { Me, SandboxOffer } from "../types";

/** The BRD's propagation target: after this, say it's slow and offer to continue. */
const TARGET_MS = 30_000;

type Phase =
  | { kind: "idle" }
  | { kind: "creating" }
  | { kind: "propagating"; visible: number; total: number; elapsedMs: number }
  | { kind: "error"; message: string };

interface Props {
  /** Signed in; `me` is the latest /v1/me (null if it couldn't be read). */
  onReady: (session: Session, me: Me | null) => void;
  /** e.g. "Your demo sandbox expired." */
  notice?: string | null;
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

/** The public front door: "Try the demo" creates a temporary real user, then waits, showing
 * progress, until its grants have propagated (app DB → Debezium → Kafka → authz-sync →
 * OpenFGA) and /v1/me lists every company. */
export function Welcome({ onReady, notice, pollMs = 1000, now = Date.now }: Props) {
  const [offer, setOffer] = useState<SandboxOffer | "off" | null>(null);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const alive = useRef(true);
  const latest = useRef<{ session: Session; me: Me | null } | null>(null);

  useEffect(() => {
    alive.current = true;
    sandboxOffer()
      .then((o) => alive.current && setOffer(o))
      .catch(() => alive.current && setOffer("off"));
    return () => {
      alive.current = false;
    };
  }, []);

  async function start() {
    setPhase({ kind: "creating" });
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
    latest.current = { session, me: null };
    const ids = new Set(session.companies);
    const started = now();
    while (alive.current) {
      let m: Me | null = null;
      try {
        m = await fetchMe(session.token);
      } catch {
        // transient: keep polling
      }
      if (!alive.current) return;
      if (m) latest.current = { session, me: m };
      const visible = m ? readableCount(m, ids) : 0;
      if (m && visible >= ids.size) {
        onReady(session, m);
        return;
      }
      setPhase({ kind: "propagating", visible, total: ids.size, elapsedMs: now() - started });
      await new Promise((r) => setTimeout(r, pollMs));
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
        Creating your user…
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

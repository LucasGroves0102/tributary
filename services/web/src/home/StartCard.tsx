import type { Readable } from "../access";
import { CompanySearch } from "../components/CompanySearch";
import { go, href, PROJECT_STEPS } from "../route";
import type { AlertUnread, SavedWorkspace } from "../types";

const CIK = /^\d{10}$/;

/** The analyses to continue: projects (workspaces with `project`), newest change first, with how
 * many of their steps the user marked done. Reads only the project's company and ticks, as
 * analysis/project.ts restoreProject does, so that module (and its thesis template) stays out of
 * the shell's first download. */
export function analysesOf(list: SavedWorkspace[]): { id: string; name: string; companyId: string; done: number }[] {
  return list.flatMap((ws) => {
    const p = ws.project as { companyId?: unknown; done?: unknown } | null | undefined;
    if (!p || typeof p.companyId !== "string" || !CIK.test(p.companyId)) return [];
    const done = new Set((Array.isArray(p.done) ? p.done : []).filter((d) => (PROJECT_STEPS as readonly unknown[]).includes(d))).size;
    const edited = [ws.updated_at, ...ws.items.map((i) => i.updated_at)].filter(Boolean).sort().at(-1) ?? ws.updated_at;
    return [{ id: ws.workspace_id, name: ws.name, companyId: p.companyId, done, edited }];
  }).sort((a, b) => b.edited.localeCompare(a.edited)).map(({ edited: _, ...r }) => r);
}

/** Home's first step (F6, #127): the recommended start, an analysis of one company, asked for
 * directly (a company picker that opens New analysis on it, L5's route); the analyses already
 * under way, to continue; and the new filing alerts. The other ways in stay as quiet buttons in
 * the page bar. */
export function StartCard({ token, readable, workspaces, alerts, label }: {
  token: string;
  /** companies whose financials the user can read: the only ones offered (as New analysis) */
  readable: Readable;
  /** saved workspaces, when they've loaded */
  workspaces: SavedWorkspace[] | null;
  alerts: AlertUnread | null | undefined;
  label: (companyId: string) => string;
}) {
  const analyses = workspaces ? analysesOf(workspaces).slice(0, 3) : [];
  return (
    <section className="card start-card" aria-labelledby="start-h">
      <div className="card-b">
        <div className="start-main">
          <div className="grow">
            <h2 id="start-h">{analyses.length ? "Continue an analysis, or start one" : "Start an analysis"}</h2>
            <p className="muted small">
              One company in eight guided steps: what it sells, its numbers, the latest earnings and what changed, peers, a model,
              and your thesis checked against the filings. Then Tributary tells you when it files again.
            </p>
          </div>
          <div className="start-pick">
            <CompanySearch token={token} label="Company to analyse" placeholder="Start an analysis: company or ticker" readable={readable}
              onPick={(id) => go({ view: "analysis", start: id })} />
          </div>
        </div>
        {analyses.length > 0 && (
          <ul className="start-projects" aria-label="Your analyses">
            {analyses.map((a) => (
              <li key={a.id}>
                <a href={href({ view: "analysis", projectId: a.id })}>
                  <b>{label(a.companyId)}</b> <span className="faint small">{a.done} of {PROJECT_STEPS.length} steps done</span>
                </a>
              </li>
            ))}
            <li><a className="faint small" href={href({ view: "analysis" })}>All analyses</a></li>
          </ul>
        )}
        {alerts && alerts.unread > 0 && (
          <p className="start-more small">
            <a className="start-alerts" href={href({ view: "alerts" })}>
              {alerts.unread} new filing alert{alerts.unread === 1 ? "" : "s"}
            </a>{" "}
            <span className="faint">for companies you watch or analyse</span>
          </p>
        )}
      </div>
    </section>
  );
}

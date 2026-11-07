import { useEffect, useState } from "react";

import { ApiError, failureReason, workspace as fetchWorkspace } from "../api";
import { leftText, useAllowanceContext } from "../assistant/useAllowance";
import { ErrorPanel, Skeleton } from "../components/States";
import { ago, day } from "../format";
import { href } from "../route";
import { useNewerFilings } from "../thesis/useRevisit";
import type { SavedWorkspaceFull } from "../types";
import { isPageKind, KIND_LABELS, KIND_PAGES } from "../workspaces/model";
import { NewWork } from "../workspaces/NewWork";
import type { Saved } from "../workspaces/useSaved";
import { REPORT_QUESTIONS } from "./model";
import type { ReportWriter } from "./useReportWriter";

/** Items a report covers (services/agent report.py MAX_REPORT_ITEMS). */
export const MAX_REPORT_ITEMS = 8;

interface Props {
  token: string;
  workspaceId: string;
  writer: ReportWriter;
  onOpen: (itemId: string) => void;
  /** bumps when saved work changed elsewhere (a report saved, an item renamed) */
  version: number;
  /** New thesis / New model (null: saved work unavailable) */
  saved: Saved | null;
}

type Loaded =
  | { state: "loading" }
  | { state: "ok"; ws: SavedWorkspaceFull }
  | { state: "error"; message: string; notFound: boolean };

/** One workspace: your theses and models, its saved items and reports; "New thesis", "New
 * model" and "Write a report". */
export function WorkspacePage({ token, workspaceId, writer, onOpen, version, saved }: Props) {
  const { allowance } = useAllowanceContext();
  const [loaded, setLoaded] = useState<Loaded>({ state: "loading" });
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let live = true;
    fetchWorkspace(token, workspaceId)
      .then((ws) => live && setLoaded({ state: "ok", ws }))
      .catch((e) => live && setLoaded({
        state: "error",
        message: failureReason(e),
        notFound: e instanceof ApiError && e.status === 404,
      }));
    return () => {
      live = false;
    };
  }, [token, workspaceId, version, retry]);
  // Revisit: theses whose company filed a 10-K/10-Q since their last check
  const theses = loaded.state === "ok" ? loaded.ws.items.filter((i) => i.kind === "thesis").map((i) => i.item_id) : [];
  const newer = useNewerFilings(token, theses);

  if (loaded.state === "loading") return <div className="page"><Skeleton lines={6} label="Loading the workspace" /></div>;
  if (loaded.state === "error") {
    return (
      <div className="page">
        {loaded.notFound ? (
          <p className="empty">This workspace doesn't exist, or isn't yours. Workspaces are visible only to their owner.</p>
        ) : (
          <ErrorPanel what="the workspace" detail={loaded.message} onRetry={() => setRetry((r) => r + 1)} />
        )}
      </div>
    );
  }
  const { ws } = loaded;
  // a report is written from charts, comps and screens (services/agent report.py)
  const items = ws.items.filter((i) => isPageKind(i.kind));
  const reports = ws.items.filter((i) => i.kind === "report");
  const written = ws.items.filter((i) => i.kind === "thesis" || i.kind === "model");
  const run = writer.run;
  const busy = run.status === "writing";
  const here = run.status !== "idle" && run.workspaceId === workspaceId;

  return (
    <div className="page">
      <div className="row wrap">
        <h1 className="grow">{ws.name}</h1>
        {saved && <NewWork token={token} saved={saved} workspaceId={workspaceId} />}
        <button
          className="btn primary"
          disabled={busy || items.length === 0}
          title={items.length ? undefined : "Save a chart, comps table or screen here first"}
          onClick={() => writer.start(workspaceId, null)}
        >
          ✦ Write a report
        </button>
      </div>
      <p className="muted small">
        The assistant writes a research note from the saved items below: a summary, a section per
        item with the chart or table drawn in place, and what changed in the latest filings where
        your plan includes them. Every number is checked against the data and cited; anything it
        can't verify is flagged. A report counts as {REPORT_QUESTIONS} assistant questions{allowance ? ` (you have ${leftText(allowance)})` : ""}
        {items.length > MAX_REPORT_ITEMS ? ` and covers the first ${MAX_REPORT_ITEMS} items` : ""}.
      </p>
      {ws.project && (
        <p className="report-status" role="note">
          This workspace is an analysis project: its guided steps, thesis and model open in the project.{" "}
          <a className="btn sm" href={href({ view: "analysis", projectId: workspaceId })}>Open the analysis</a>
        </p>
      )}
      {here && <WritingStatus writer={writer} />}

      <section className="card">
        <div className="card-h">
          <h2>Theses and models</h2>
          <span className="sub">written by you; a thesis can embed this workspace's charts, comps, screens and models</span>
        </div>
        {written.length ? (
          <ul className="ws-list">
            {written.map((i) => (
              <li key={i.item_id}>
                <span className={`tag ${i.kind}`}>{KIND_LABELS[i.kind]}</span>{" "}
                <button className="link" onClick={() => onOpen(i.item_id)} title={`Open in ${KIND_PAGES[i.kind]}`}>
                  <b>{i.name}</b>
                </button>{" "}
                <span className="faint">updated {ago(i.updated_at)}</span>
                {newer[i.item_id] && (
                  <>
                    {" "}
                    <button className="link revisit small" onClick={() => onOpen(i.item_id)}
                      title="Open the thesis to check it again against the new filing">
                      New {newer[i.item_id]!.form} filed {day(newer[i.item_id]!.filed, true)} since your last check — re-check?
                    </button>
                  </>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="empty">
            No thesis or model yet. A model forecasts a company's revenue and operating income from its history and your
            assumptions; a thesis is your argument in Markdown, with the charts and models drawn in place.
          </p>
        )}
      </section>

      <section className="card">
        <div className="card-h">
          <h2>Reports</h2>
          <span className="sub">written by the assistant from this workspace</span>
        </div>
        {reports.length ? (
          <ul className="ws-list">
            {reports.map((r) => (
              <li key={r.item_id}>
                <span className="tag report">Report</span>{" "}
                <button className="link" onClick={() => onOpen(r.item_id)}>
                  <b>{r.name}</b>
                </button>{" "}
                <span className="faint">updated {ago(r.updated_at)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="empty">No reports yet. Write one from the items below.</p>
        )}
      </section>

      <section className="card">
        <div className="card-h">
          <h2>Saved items</h2>
          <span className="sub">{items.length} {items.length === 1 ? "item" : "items"}, oldest first</span>
        </div>
        {items.length ? (
          <ul className="ws-list">
            {items.map((i) => (
              <li key={i.item_id}>
                <span className={`tag ${i.kind}`}>{KIND_LABELS[i.kind]}</span>{" "}
                <button className="link" onClick={() => onOpen(i.item_id)} title={`Open in ${KIND_PAGES[i.kind]}`}>
                  <b>{i.name}</b>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="empty">
            Nothing saved here yet. Use Save on the <a href={href({ view: "chart" })}>Chart builder</a>,{" "}
            <a href={href({ view: "compare" })}>Compare</a> or <a href={href({ view: "companies" })}>Companies</a>.
          </p>
        )}
      </section>
    </div>
  );
}

/** Progress of the report being written, or why it stopped. */
export function WritingStatus({ writer }: { writer: ReportWriter }) {
  const run = writer.run;
  if (run.status === "writing") {
    return (
      <div className="report-status" role="status" aria-live="polite">
        <div className="row">
          <span className="live" />
          <b className="grow">
            Writing the report… {run.tools} data {run.tools === 1 ? "lookup" : "lookups"} so far
          </b>
          <button className="btn sm ghost" onClick={writer.stop}>
            Stop
          </button>
        </div>
        {run.text && <p className="report-preview faint">{run.text}</p>}
      </div>
    );
  }
  if (run.status === "error") {
    return (
      <div className="report-status error" role="alert">
        {run.message}
      </div>
    );
  }
  if (run.status === "done") {
    return (
      <div className="report-status" role="status">
        Report saved{run.ok ? ", every figure verified." : `; ${run.unverified} figure${run.unverified === 1 ? "" : "s"} couldn't be verified and are flagged.`}{" "}
        <a href={href({ view: "report", itemId: run.itemId })}>Open it</a>
      </div>
    );
  }
  return null;
}

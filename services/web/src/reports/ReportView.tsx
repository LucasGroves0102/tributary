import { useEffect, useMemo, useState } from "react";

import { ApiError, openItem, screenerColumns } from "../api";
import { slug } from "../chart/export";
import { catalog } from "../chart/metrics";
import type { ChartState } from "../chart/model";
import { ErrorPanel, Skeleton } from "../components/States";
import { download } from "../download";
import { ago, day } from "../format";
import { renderAnswer } from "../markdown";
import { href } from "../route";
import type { Citation, SavedItemFull } from "../types";
import { Embed } from "./Embeds";
import { reportMarkdown, restoreReport, segments, unverifiedFigures, verificationSummary, type Report } from "./model";
import type { ReportWriter } from "./useReportWriter";
import { WritingStatus } from "./WorkspacePage";

interface Props {
  token: string;
  itemId: string;
  writer: ReportWriter;
  /** company id → ticker or name */
  label: (companyId: string) => string;
  /** companies whose financials the viewer can read (comps embeds) */
  readable: ReadonlySet<string>;
  onOpenChart: (s: ChartState) => void;
}

type Loaded =
  | { state: "loading" }
  | { state: "ok"; item: SavedItemFull; report: Report }
  | { state: "error"; message: string; notFound: boolean };

/** A report the assistant wrote: its note with every saved item drawn in place, figures it
 * couldn't verify highlighted, its sources; Write again, Markdown download and Print (light
 * palette, no app chrome). */
export function ReportView({ token, itemId, writer, label, readable, onOpenChart }: Props) {
  const [loaded, setLoaded] = useState<Loaded>({ state: "loading" });
  const [version, setVersion] = useState(0);
  const [hasTtm, setHasTtm] = useState<(m: string) => boolean>(() => () => true);
  const run = writer.run;
  // a finished rewrite of this report: read it again
  const rewritten = run.status === "done" && run.itemId === itemId ? run : null;

  useEffect(() => {
    let live = true;
    openItem(token, itemId)
      .then((item) => {
        if (!live) return;
        const report = item.kind === "report" ? restoreReport(item.state) : null;
        setLoaded(report ? { state: "ok", item, report } : { state: "error", message: "This item isn't a report.", notFound: false });
      })
      .catch((e) => live && setLoaded({
        state: "error",
        message: e instanceof ApiError ? e.message : "the Query API didn't answer",
        notFound: e instanceof ApiError && e.status === 404,
      }));
    return () => {
      live = false;
    };
  }, [token, itemId, version, rewritten]);

  useEffect(() => {
    let live = true;
    screenerColumns(token)
      .then((r) => {
        const ttm = new Map(catalog(r.columns).map((m) => [m.name, m.ttm]));
        if (live) setHasTtm(() => (m: string) => ttm.get(m) ?? true);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [token]);

  if (loaded.state === "loading") return <div className="page"><Skeleton lines={8} label="Loading the report" /></div>;
  if (loaded.state === "error") {
    return (
      <div className="page">
        {loaded.notFound ? (
          <p className="empty">This report doesn't exist, or isn't yours. Reports are visible only to their owner.</p>
        ) : (
          <ErrorPanel what="the report" detail={loaded.message} onRetry={() => setVersion((v) => v + 1)} />
        )}
      </div>
    );
  }
  const { item, report } = loaded;
  const writingThis = run.status === "writing" && run.reportId === itemId;
  return (
    <div className="page report-page">
      <div className="row no-print">
        <a className="btn sm ghost" href={href({ view: "workspace", workspaceId: item.workspace_id })}>
          ← {item.workspace_name ?? "Workspace"}
        </a>
        <span className="grow" />
        <button
          className="btn sm"
          onClick={() => download(`${slug(item.name)}.md`, new Blob([reportMarkdown(report, item.name, label)], { type: "text/markdown" }))}
        >
          Download Markdown
        </button>
        <button className="btn sm" onClick={() => window.print()}>
          Print / PDF
        </button>
        <button
          className="btn sm primary"
          disabled={run.status === "writing"}
          title={`Writes the report again from the workspace's saved items; counts as ${report.questions} assistant questions`}
          onClick={() => writer.start(item.workspace_id, itemId)}
        >
          Write again
        </button>
      </div>
      {(writingThis || (run.status === "error" && run.reportId === itemId)) && <WritingStatus writer={writer} />}
      <ReportBody report={report} name={item.name} label={label} token={token} readable={readable} hasTtm={hasTtm} onOpenChart={onOpenChart} />
    </div>
  );
}

export function ReportBody({ report, name, label, token, readable, hasTtm, onOpenChart }: {
  report: Report;
  name: string;
  label: (companyId: string) => string;
  token: string;
  readable: ReadonlySet<string>;
  hasTtm: (metric: string) => boolean;
  onOpenChart: (s: ChartState) => void;
}) {
  const parts = useMemo(() => segments(report), [report]);
  const unverified = useMemo(() => unverifiedFigures(report), [report]);
  const sum = verificationSummary(report);
  return (
    <article className="report-doc" aria-label={name}>
      <p className="report-meta">
        <span className={sum.ok ? "badge ok" : "badge warn"}>{sum.ok ? "✓ " : "⚠ "}{sum.text}</span>{" "}
        <span className="muted">
          Written {report.writtenAt ? `${day(report.writtenAt.slice(0, 10), true)} (${ago(report.writtenAt)})` : ""} by the
          assistant ({report.model}) from the workspace's saved items
          {report.asOf ? ` · data as of ${day(report.asOf.slice(0, 10), true)}` : ""}
        </span>
      </p>
      {parts.map((p, i) =>
        p.type === "text" ? (
          <Markdown key={i} markdown={p.markdown} citations={report.citations} unverified={unverified} />
        ) : (
          <Embed key={p.item.ref} token={token} item={p.item} label={label} readable={readable} hasTtm={hasTtm} onOpenChart={onOpenChart} />
        ),
      )}
      {report.citations.length > 0 && (
        <div className="sources">
          <h3>Sources</h3>
          <ul>
            {report.citations.map((c) => (
              <li key={c.accession_no}>
                <span className="form">{c.form ?? "source"}</span>
                <span className="muted">{c.filed}</span>
                {c.url ? (
                  <a href={c.url} target="_blank" rel="noopener noreferrer" className="accession">
                    {c.accession_no}
                  </a>
                ) : (
                  <span className="accession">{c.accession_no}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </article>
  );
}

function Markdown({ markdown, citations, unverified }: { markdown: string; citations: Citation[]; unverified: string[] }) {
  const html = useMemo(() => renderAnswer(markdown, citations, unverified), [markdown, citations, unverified]);
  return <div className="answer report-text" dangerouslySetInnerHTML={{ __html: html }} />;
}

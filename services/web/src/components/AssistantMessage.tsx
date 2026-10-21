import { useMemo, useState } from "react";

import { renderAnswer } from "../markdown";
import type { Answer, Citation, ToolCall, Verification } from "../types";

/** A change the assistant made in the UI (chart, screen, watchlist), shown under its message
 * with Undo (assistant/useUiActions). */
export interface ActionRecord {
  id: number;
  label: string;
}

export interface AssistantTurn {
  id: number;
  role: "assistant";
  rounds: Record<number, string>;
  tools: (ToolCall & { round: number })[];
  answer?: Answer;
  error?: string;
  actions: ActionRecord[];
}

const TOOL_LABEL: Record<string, string> = {
  whoami: "Checked access",
  search_companies: "Searched companies",
  get_financials: "Read financials",
  list_filings: "Listed filings",
  get_section: "Read filing section",
  get_section_changes: "Compared with the prior filing",
  search_filings: "Searched filings",
  propose_ui_action: "Changed the screen",
};

export function AssistantMessage({
  turn,
  undone,
  onUndo,
}: {
  turn: AssistantTurn;
  undone?: ReadonlySet<number>;
  onUndo?: (actionId: number) => void;
}) {
  const finalRound = turn.answer ? turn.answer.rounds - 1 : maxRound(turn);
  const text = turn.answer?.text ?? turn.rounds[finalRound] ?? "";
  const citations = turn.answer?.citations ?? [];
  const unverified = useMemo(
    () =>
      turn.answer?.verification?.figures.filter((f) => f.status === "unverified").map((f) => f.text) ??
      [],
    [turn.answer],
  );
  const html = useMemo(
    () => renderAnswer(text, citations, unverified),
    [text, citations, unverified],
  );
  const pending = !turn.answer && !turn.error;

  return (
    <article className="message assistant" aria-busy={pending}>
      <Trace turn={turn} finalRound={finalRound} />
      {turn.error ? (
        <p className="error">{turn.error}</p>
      ) : text ? (
        <div className="answer" dangerouslySetInnerHTML={{ __html: html }} />
      ) : (
        <p className="muted thinking">Working…</p>
      )}
      {turn.actions.map((a) => (
        <Action key={a.id} action={a} undone={undone?.has(a.id) ?? false} onUndo={onUndo} />
      ))}
      {turn.answer && <Sources citations={usedCitations(turn.answer)} />}
      {turn.answer && <Meta answer={turn.answer} />}
    </article>
  );
}

function Action({
  action,
  undone,
  onUndo,
}: {
  action: ActionRecord;
  undone: boolean;
  onUndo?: (id: number) => void;
}) {
  return (
    <div className={undone ? "act undone" : "act"}>
      <span className="grow">
        {undone ? "↺ Undone" : "✓ Applied"}: {action.label}
      </span>
      {!undone && onUndo && (
        <button className="btn sm" onClick={() => onUndo(action.id)}>
          Undo
        </button>
      )}
    </div>
  );
}

function maxRound(turn: AssistantTurn): number {
  const rounds = [...Object.keys(turn.rounds).map(Number), ...turn.tools.map((t) => t.round)];
  return rounds.length ? Math.max(...rounds) : 0;
}

function Trace({ turn, finalRound }: { turn: AssistantTurn; finalRound: number }) {
  const [open, setOpen] = useState(false);
  if (!turn.tools.length) return null;
  const denied = turn.tools.filter((t) => t.is_error).length;
  const notes = Object.entries(turn.rounds).filter(([r]) => Number(r) !== finalRound);

  return (
    <div className="trace">
      <button className="trace-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="chevron">{open ? "▾" : "▸"}</span>
        {turn.tools.length} tool call{turn.tools.length === 1 ? "" : "s"}
        {denied > 0 && <span className="denied-count">{denied} denied</span>}
      </button>
      {open && (
        <ol className="trace-list">
          {notes.map(([r, note]) => (
            <li key={`note-${r}`} className="note">
              {note}
            </li>
          ))}
          {turn.tools.map((t, i) => (
            <li key={i} className={t.is_error ? "tool denied" : "tool allowed"}>
              <span className="status">{t.is_error ? "denied" : "ok"}</span>
              <span>{TOOL_LABEL[t.name] ?? t.name}</span>
              <code>{argsSummary(t.input)}</code>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function argsSummary(input: Record<string, unknown>): string {
  return Object.entries(input)
    .map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : String(v)}`)
    .join("  ");
}

/** Citations whose accession number appears in the answer text. */
function usedCitations(answer: Answer): Citation[] {
  return answer.citations
    .filter((c) => answer.text.includes(c.accession_no))
    .sort((a, b) => (b.filed ?? "").localeCompare(a.filed ?? ""));
}

function Sources({ citations }: { citations: Citation[] }) {
  if (!citations.length) return null;
  return (
    <div className="sources">
      <h3>Sources</h3>
      <ul>
        {citations.map((c) => (
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
  );
}

function VerifiedBadge({ v }: { v: Verification }) {
  const figures = v.figures.length;
  if (!v.used_data && !figures) return null;
  const bad = v.figures.filter((f) => f.status === "unverified").length;
  const problems = [
    bad && `${bad} figure${bad === 1 ? "" : "s"} not in the data`,
    v.unverified_accessions.length &&
      `${v.unverified_accessions.length} unknown filing${v.unverified_accessions.length === 1 ? "" : "s"}`,
    v.used_data && !v.freshness && "no freshness",
  ].filter(Boolean);
  const title = v.figures.map((f) => `${f.text}: ${f.status}`).join("\n");
  return v.ok ? (
    <span className="badge ok" title={title}>
      ✓ {figures} figure{figures === 1 ? "" : "s"} verified against the data
    </span>
  ) : (
    <span className="badge warn" title={title}>
      ⚠ {problems.join(", ")}
    </span>
  );
}

function Meta({ answer }: { answer: Answer }) {
  const used = usedCitations(answer);
  const latest = used.map((c) => c.filed).filter(Boolean).sort().at(-1);
  const tokens = answer.usage.input_tokens + answer.usage.cache_read_input_tokens +
    answer.usage.cache_creation_input_tokens + answer.usage.output_tokens;
  return (
    <footer className="meta">
      {answer.verification && <VerifiedBadge v={answer.verification} />}
      {latest && <span>Data as of filing {latest}</span>}
      <span>{answer.model}</span>
      <span>{tokens.toLocaleString()} tokens</span>
      {answer.cost_usd !== null && <span>${answer.cost_usd.toFixed(4)}</span>}
    </footer>
  );
}

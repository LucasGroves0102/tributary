/** The decision journal (Query API query_api/journal.py): one holding's entries with a form to
 * write the next (kind, the day decided, why, what would make you sell and the red lines it
 * hangs on, confidence 1-5), and the portfolio page's latest entries. For 24 hours an entry can
 * be edited or deleted; after that it is kept as written and "Correct" writes a new entry beside
 * it (the database refuses anything else). The user's own words render as plain text. */

import { useEffect, useState, type FormEvent } from "react";

import { addJournalEntry, deleteJournalEntry, editJournalEntry, failureReason, journal, redLines } from "../api";
import { ruleText } from "../alerts/alerts";
import { Choices } from "../components/Choices";
import { ErrorPanel, Skeleton } from "../components/States";
import { day, localTime } from "../format";
import { href } from "../route";
import type { Holding, JournalEntry, JournalKind, RedLine } from "../types";
import { ConfirmDelete } from "../workspaces/Actions";
import { CONFIDENCE_WORDS, editWords, KIND_WORDS, today, withCorrections } from "./portfolio";

type Load = { state: "loading" } | { state: "ok"; entries: JournalEntry[] } | { state: "off" } | { state: "error"; message: string };

interface Subject {
  companyId: string | null;
  symbol: string | null;
}

function useJournal(token: string, subject: Subject | null, limit: number, attempt: number): Load {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  useEffect(() => {
    let live = true;
    journal(token, { ...(subject?.companyId ? { companyId: subject.companyId } : subject?.symbol ? { symbol: subject.symbol } : {}), limit })
      .then((j) => live && setLoad(j ? { state: "ok", entries: j.entries } : { state: "off" }))
      .catch((e) => live && setLoad({ state: "error", message: failureReason(e) }));
    return () => {
      live = false;
    };
  }, [token, subject?.companyId, subject?.symbol, limit, attempt]);
  return load;
}

/** One holding's journal, with the form (the Review button opens it as a review). */
export function JournalView({ token, subject, reviewDue, prompt, onWritten, monitor }: {
  token: string;
  subject: Subject;
  /** a review is due: the form offers to write it */
  reviewDue: boolean;
  /** open the form now, as this kind (the holding page's "Write the review") */
  prompt?: JournalKind | null;
  onWritten: () => void;
  /** where this company's red lines are set: its analysis's Monitor step, or New analysis */
  monitor?: string;
}) {
  const [attempt, setAttempt] = useState(0);
  const load = useJournal(token, subject, 100, attempt);
  const [form, setForm] = useState<{ kind: JournalKind; corrects?: JournalEntry; edit?: JournalEntry } | null>(null);
  useEffect(() => {
    if (prompt) setForm({ kind: prompt });
  }, [prompt]);
  const reload = () => {
    setAttempt((n) => n + 1);
    onWritten();
  };
  if (load.state === "loading") return <Skeleton lines={3} label="Loading the journal" />;
  if (load.state === "off") return <p className="empty">The journal isn't available on this server yet.</p>;
  if (load.state === "error") return <ErrorPanel what="the journal" detail={load.message} onRetry={() => setAttempt((n) => n + 1)} />;
  return (
    <div className="journal">
      {form ? (
        <EntryForm token={token} subject={subject} start={form} monitor={monitor} onCancel={() => setForm(null)} onSaved={() => {
          setForm(null);
          reload();
        }} />
      ) : (
        <div className="row wrap">
          <button className="btn" onClick={() => setForm({ kind: reviewDue ? "review" : "note" })}>
            {reviewDue ? "Write the review" : "Write an entry"}
          </button>
        </div>
      )}
      {!load.entries.length ? (
        <p className="empty">Nothing written yet. Say why you hold it and what would make you sell, while it's fresh.</p>
      ) : (
        <ol className="journal-list">
          {withCorrections(load.entries).map(({ entry, corrections }) => (
            <li key={entry.id} className="journal-item">
              <Entry token={token} e={entry} onCorrect={() => setForm({ kind: entry.kind, corrects: entry })}
                onEdit={() => setForm({ kind: entry.kind, edit: entry })} onChanged={reload} />
              {corrections.length > 0 && (
                <ol className="journal-corrections" aria-label="Corrections">
                  {corrections.map((c) => (
                    <li key={c.id}>
                      <Entry token={token} e={c} correction onCorrect={() => setForm({ kind: c.kind, corrects: entry })}
                        onEdit={() => setForm({ kind: c.kind, edit: c })} onChanged={reload} />
                    </li>
                  ))}
                </ol>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function Entry({ token, e, correction = false, onCorrect, onEdit, onChanged }: {
  token: string;
  e: JournalEntry;
  correction?: boolean;
  onCorrect: () => void;
  onEdit: () => void;
  onChanged: () => void;
}) {
  const [confirm, setConfirm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const remove = async () => {
    setConfirm(false);
    try {
      await deleteJournalEntry(token, e.id);
      onChanged();
    } catch (err) {
      setError(`Couldn't delete it: ${failureReason(err)}.`);
    }
  };
  return (
    <article className={correction ? "entry correction" : "entry"}>
      <header className="entry-head">
        <span className="entry-day">{day(e.decided_on, true)}</span>
        <b className="entry-kind">{correction ? `Correction: ${KIND_WORDS[e.kind].toLowerCase()}` : KIND_WORDS[e.kind]}</b>
        {e.confidence !== null && <span className="entry-confidence" title="Your confidence, 1 to 5">Confidence {e.confidence} of 5, {CONFIDENCE_WORDS[e.confidence]!.toLowerCase()}</span>}
      </header>
      <p className="entry-why">{e.why}</p>
      {(e.sell_rules || e.red_lines.length > 0) && (
        <div className="entry-sell">
          <span className="entry-label">What would make me sell</span>
          {e.sell_rules && <p>{e.sell_rules}</p>}
          {e.red_lines.length > 0 && (
            <ul className="entry-redlines">
              {e.red_lines.map((r) => <li key={r.rule_id}>Red line: {r.label ? `${r.label}, ` : ""}{ruleText(r)}</li>)}
            </ul>
          )}
        </div>
      )}
      <footer className="entry-foot faint small">
        <span>Written {localTime(e.created_at, true)}{e.edited_at ? ", edited" : ""}{e.kept ? "; kept as written" : ""}</span>
        {/* the edit window in view, not only a hover (Riley, test 10, finding 49) */}
        {!e.kept && <span className="entry-window">{editWords(e)}</span>}
        {e.kept ? (
          !correction && <button className="btn sm ghost" onClick={onCorrect}>Correct</button>
        ) : (
          <>
            <button className="btn sm ghost" onClick={onEdit}>Edit</button>
            <button className="btn sm ghost danger" onClick={() => setConfirm(true)}>Delete</button>
          </>
        )}
      </footer>
      {confirm && (
        <ConfirmDelete text="Delete this entry?" detail="It hasn't been kept yet, so it can go. After 24 hours entries stay as written."
          onYes={() => void remove()} onNo={() => setConfirm(false)} />
      )}
      {error && <p className="err small" role="alert">{error}</p>}
    </article>
  );
}

const KINDS: [JournalKind, string][] = (Object.keys(KIND_WORDS) as JournalKind[]).map((k) => [k, KIND_WORDS[k]]);

function EntryForm({ token, subject, start, onCancel, onSaved, monitor }: {
  token: string;
  subject: Subject;
  monitor?: string;
  start: { kind: JournalKind; corrects?: JournalEntry; edit?: JournalEntry };
  onCancel: () => void;
  onSaved: () => void;
}) {
  const base = start.edit ?? start.corrects;
  const [kind, setKind] = useState<JournalKind>(start.kind);
  const [decided, setDecided] = useState(start.edit?.decided_on ?? today());
  const [why, setWhy] = useState(base?.why ?? "");
  const [sell, setSell] = useState(base?.sell_rules ?? "");
  const [confidence, setConfidence] = useState<string>(base?.confidence ? String(base.confidence) : "");
  const [lines, setLines] = useState<RedLine[] | null>(null);
  const [chosen, setChosen] = useState<Set<string>>(new Set(base?.red_lines.map((r) => r.rule_id) ?? []));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!subject.companyId) return;
    let live = true;
    redLines(token, subject.companyId)
      .then((r) => live && setLines(r?.red_lines ?? []))
      .catch(() => live && setLines([]));
    return () => {
      live = false;
    };
  }, [token, subject.companyId]);

  const submit = async (ev: FormEvent) => {
    ev.preventDefault();
    if (!why.trim()) return setError("Say why, in a sentence or more.");
    setBusy(true);
    setError(null);
    const fields = {
      kind, why: why.trim(), decided_on: decided, sell_rules: sell.trim() || null,
      confidence: confidence ? Number(confidence) : null,
      ...(subject.companyId ? { red_line_ids: [...chosen] } : {}),
    };
    try {
      if (start.edit) await editJournalEntry(token, start.edit.id, fields);
      else {
        await addJournalEntry(token, {
          ...(subject.companyId ? { company_id: subject.companyId } : { symbol: subject.symbol! }),
          ...fields, ...(start.corrects ? { corrects: start.corrects.id } : {}),
        });
      }
      onSaved();
    } catch (err) {
      setError(`Couldn't save it: ${failureReason(err)}.`);
    } finally {
      setBusy(false);
    }
  };
  const title = start.edit ? "Edit the entry" : start.corrects ? `Correct the entry of ${day(start.corrects.decided_on, true)}` : "New entry";
  return (
    <form className="pf-form entry-form" onSubmit={(e) => void submit(e)} aria-label={title}>
      <h3>{title}</h3>
      {start.corrects && <p className="faint small">The entry stays as written; this one goes beside it.</p>}
      <div className="pf-fields">
        <label className="pf-field">
          <span>Kind</span>
          <select value={kind} onChange={(e) => setKind(e.target.value as JournalKind)}>
            {KINDS.map(([k, w]) => <option key={k} value={k}>{w}</option>)}
          </select>
        </label>
        <label className="pf-field">
          <span>Decided on</span>
          <input type="date" className="field" value={decided} max={today()} onChange={(e) => setDecided(e.target.value)} />
        </label>
        <div className="pf-field">
          <span id="pf-conf">Confidence</span>
          <Choices label="Confidence, 1 (low) to 5 (high)" value={confidence || "0"} set={(v) => setConfidence(v === "0" ? "" : v)}
            options={[["0", "Not said"], ["1", "1"], ["2", "2"], ["3", "3"], ["4", "4"], ["5", "5"]]} />
        </div>
        <label className="pf-field full">
          <span>Why</span>
          <textarea className="field" rows={4} maxLength={4000} value={why} onChange={(e) => setWhy(e.target.value)}
            placeholder={kind === "review" ? "What the report said, and whether the reasons still hold" : "Your reasons, in your words"} />
        </label>
        <label className="pf-field full">
          <span>What would make me sell <span className="faint">(optional)</span></span>
          <textarea className="field" rows={2} maxLength={2000} value={sell} onChange={(e) => setSell(e.target.value)}
            placeholder="Margins below 50% two quarters running; the thesis's customer leaves" />
        </label>
        {subject.companyId && lines !== null && (
          <fieldset className="pf-field full entry-lines">
            <legend>Tie it to your red lines</legend>
            {lines.length ? lines.map((r) => (
              <label key={r.rule_id} className="check">
                <input type="checkbox" checked={chosen.has(r.rule_id)} onChange={(e) => {
                  const next = new Set(chosen);
                  if (e.target.checked) next.add(r.rule_id);
                  else next.delete(r.rule_id);
                  setChosen(next);
                }} />
                {r.label ? `${r.label}: ` : ""}{ruleText(r)}
              </label>
            )) : (
              <p className="faint small">
                {/* red lines live only in an analysis's Monitor step: Settings has none (Riley, test 10, finding 12) */}
                No red lines on this company yet: they're set in the Monitor step of its analysis.{" "}
                <a href={monitor ?? (subject.companyId ? href({ view: "analysis", start: subject.companyId }) : href({ view: "analysis" }))}>
                  {monitor?.includes("/monitor") ? "Open its Monitor step" : "Start an analysis"}
                </a>
              </p>
            )}
          </fieldset>
        )}
      </div>
      {error && <p className="err small" role="alert">{error}</p>}
      <div className="row">
        <button className="btn primary" type="submit" disabled={busy}>{busy ? "Saving…" : start.edit ? "Save" : "Add to the journal"}</button>
        <button className="btn ghost" type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

/** A long entry cut at a word, never mid-word ("gross margin f…": Riley, test 10, finding 49). */
export function snip(text: string, max: number): string {
  if (text.length <= max) return text;
  const cut = text.slice(0, max + 1);
  const at = cut.lastIndexOf(" ");
  return `${(at > max * 0.6 ? cut.slice(0, at) : text.slice(0, max)).replace(/[\s,;:.]+$/, "")}…`;
}

/** The portfolio page's latest entries across holdings, each linking to its holding. */
export function RecentEntries({ token, holdings, name, label }: {
  token: string;
  holdings: Holding[];
  name: (h: Holding) => string;
  /** a company's name (directory) for an entry about one no longer held */
  label: (companyId: string) => string;
}) {
  const load = useJournal(token, null, 5, 0);
  if (load.state === "loading") return <Skeleton lines={2} label="Loading the journal" />;
  if (load.state !== "ok") return null;
  if (!load.entries.length) return <p className="empty">No entries yet. Open a holding to write why you hold it.</p>;
  const of = (e: JournalEntry) => holdings.find((h) => (e.company_id ? h.company_id === e.company_id : !h.company_id && h.symbol === e.symbol));
  return (
    <ul className="journal-recent">
      {load.entries.map((e) => {
        const h = of(e);
        return (
          <li key={e.id}>
            <span className="entry-day">{day(e.decided_on, true)}</span>
            <b>{e.corrects ? "Correction" : KIND_WORDS[e.kind]}</b>
            {h ? <a href={href({ view: "portfolio", holdingId: h.id })}>{name(h)}</a> : <span>{e.company_id ? label(e.company_id) : e.symbol}</span>}
            <span className="journal-snip">{snip(e.why, 140)}</span>
          </li>
        );
      })}
    </ul>
  );
}

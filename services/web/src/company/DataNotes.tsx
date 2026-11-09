import { useState } from "react";

import { dataNotes } from "../api";
import { Empty } from "../components/States";
import { ago, day } from "../format";
import { href } from "../route";
import type { DataNote, NoteFiling } from "../types";
import { edgarUrl, explain, groupNotes, rankOf, ROLE_LABEL, severityLabel, summaryText } from "./notes";
import { Gate } from "./Tables";
import { useLoad } from "./useLoad";

/** Notes shown per group before "Show all". */
export const NOTES_SHOWN = 6;

/** The tearsheet's Data notes tab: what the automated checks found in this company's figures,
 * in plain language, each with the filings behind it. Needs financials (notes are about
 * values); a denied load reads like every other gated tab. */
export function DataNotes({ token, companyId, name, canReadFilings }: {
  token: string;
  companyId: string;
  name: string;
  canReadFilings: boolean;
}) {
  const load = useLoad(companyId, () => dataNotes(token, companyId));
  return (
    <section className="card">
      <div className="card-h">
        <h2>Data notes</h2>
        <span className="sub">newest first · gaps, restatements, splits, overlapping periods and freshness, from automated checks</span>
      </div>
      <Gate load={load} name={name} what="Data notes">
        {() => {
          if (load?.state !== "ok") return null;
          const { notes, checked_at } = load.data;
          if (!notes.length) {
            return (
              <Empty>
                Nothing to flag for {name}: {checked_at ? `the last check of its figures (${ago(checked_at)})` : "the checks"} found no gaps,
                overlapping quarters or restatements, and the figures include every 10-K and 10-Q on EDGAR.
              </Empty>
            );
          }
          const summary = summaryText(load.data.summary);
          return (
            <>
              {summary && <p className="notes-summary">{summary}</p>}
              {groupNotes(notes).map((g) => (
                <NoteGroup key={g.key} label={g.label} notes={g.notes} companyId={companyId} canReadFilings={canReadFilings} />
              ))}
              <div className="foot">
                {checked_at ? `This company's figures were last checked ${ago(checked_at)}, the last time they changed. ` : ""}
                Notes describe the data Tributary extracted from SEC filings, not the company's business.
              </div>
            </>
          );
        }}
      </Gate>
    </section>
  );
}

/** Notes that need attention or are worth a look show first (NOTES_SHOWN of them); minor or old
 * ones (the API's rank) wait behind the button, so a group of 40 small restatements doesn't bury
 * the one that matters. Nothing is hidden for good: "Show all" lists every note. */
export function shownNotes(notes: DataNote[], all: boolean): DataNote[] {
  if (all) return notes;
  return notes.filter((n) => rankOf(n) !== "minor").slice(0, NOTES_SHOWN);
}

function NoteGroup({ label, notes, companyId, canReadFilings }: { label: string; notes: DataNote[]; companyId: string; canReadFilings: boolean }) {
  const [all, setAll] = useState(false);
  const shown = shownNotes(notes, all);
  const collapsed = all ? shownNotes(notes, false).length : shown.length;
  const minor = notes.filter((n) => rankOf(n) === "minor").length;
  return (
    <div className="notes-group">
      <h3>
        {label} <span className="faint">({notes.length}{minor ? `, ${minor} minor` : ""})</span>
      </h3>
      {shown.length > 0 && (
        <ul className="notes">
          {shown.map((n, i) => (
            <NoteItem key={`${n.kind}-${n.metric}-${n.period_end}-${i}`} note={n} companyId={companyId} canReadFilings={canReadFilings} />
          ))}
        </ul>
      )}
      {notes.length > collapsed && (
        <button className="btn sm ghost" onClick={() => setAll(!all)}>
          {all ? "Show fewer" : collapsed ? `Show all ${notes.length}` : `Show ${notes.length} minor or old note${notes.length === 1 ? "" : "s"}`}
        </button>
      )}
    </div>
  );
}

/** One note: severity, title, explanation, and links to every filing it names. */
export function NoteItem({ note, companyId, canReadFilings, company }: {
  note: DataNote;
  companyId: string;
  canReadFilings: boolean;
  /** the company's name, on lists that span companies (Home) */
  company?: string;
}) {
  const { title, text } = explain(note);
  const rank = rankOf(note);
  return (
    <li className={`note ${note.severity} rank-${rank}`}>
      <span className={`tag sev-${note.severity}`} title={rank === "attention" ? "Needs attention: severe, recent and about a headline figure" : undefined}>
        {severityLabel(note.severity)}
      </span>
      <div className="note-b">
        <div className="note-t">
          {company && (
            <>
              <a href={href({ view: "company", companyId, tab: "notes" })}>{company}</a>
              {" · "}
            </>
          )}
          <b>{title}</b>
          {note.date && <span className="faint"> · {day(note.date, true)}</span>}
        </div>
        <p>{text}</p>
        {note.filings.length > 0 && (
          <div className="note-f">
            {note.filings.slice(0, 4).map((f) => (
              <FilingLink key={`${f.role}-${f.accession_no}`} f={f} companyId={companyId} canRead={canReadFilings} />
            ))}
            {note.filings.length > 4 && <span className="faint">+{note.filings.length - 4} more</span>}
          </div>
        )}
      </div>
    </li>
  );
}

function FilingLink({ f, companyId, canRead }: { f: NoteFiling; companyId: string; canRead: boolean }) {
  const label = `${ROLE_LABEL[f.role]}: ${f.form ?? "filing"}${f.filed ? ` ${day(f.filed, true)}` : ""}`;
  const hover = `${f.form ?? "Filing"} ${f.accession_no}${f.filed ? `, filed ${day(f.filed, true)}` : ""}`;
  const edgar = edgarUrl(f);
  // the reader opens parsed 10-K/10-Q text; other filings (and missing ones) go to EDGAR
  if (canRead && f.role !== "missing" && /^10-[KQ]/.test(f.form ?? "")) {
    return (
      <a className="btn sm ghost" title={hover} href={href({ view: "filing", companyId, accessionNo: f.accession_no, section: null })}>
        {label}
      </a>
    );
  }
  if (edgar) {
    return (
      <a className="btn sm ghost" title={hover} href={edgar} target="_blank" rel="noopener noreferrer">
        {label} ↗
      </a>
    );
  }
  return (
    <span className="mono faint" title={hover}>
      {label} ({f.accession_no})
    </span>
  );
}

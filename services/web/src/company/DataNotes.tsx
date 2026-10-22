import { useState } from "react";

import { dataNotes } from "../api";
import { Empty } from "../components/States";
import { ago, day } from "../format";
import { href } from "../route";
import type { DataNote, NoteFiling } from "../types";
import { edgarUrl, explain, groupNotes, ROLE_LABEL, severityLabel } from "./notes";
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
        <span className="sub">gaps, restatements, overlapping periods, derived quarters and freshness, from automated checks</span>
      </div>
      <Gate load={load} name={name} what="Data notes">
        {() => {
          if (load?.state !== "ok") return null;
          const { notes, checked_at } = load.data;
          if (!notes.length) {
            return (
              <Empty>
                Nothing to flag for {name}: {checked_at ? `the last check (${ago(checked_at)})` : "the checks"} found no gaps,
                overlapping quarters or restatements, and the figures include every 10-K and 10-Q on EDGAR.
              </Empty>
            );
          }
          return (
            <>
              {groupNotes(notes).map((g) => (
                <NoteGroup key={g.key} label={g.label} notes={g.notes} companyId={companyId} canReadFilings={canReadFilings} />
              ))}
              <div className="foot">
                {checked_at ? `Checked ${ago(checked_at)} after the latest refresh. ` : ""}
                Notes describe the data Tributary extracted from SEC filings, not the company's business.
              </div>
            </>
          );
        }}
      </Gate>
    </section>
  );
}

function NoteGroup({ label, notes, companyId, canReadFilings }: { label: string; notes: DataNote[]; companyId: string; canReadFilings: boolean }) {
  const [all, setAll] = useState(false);
  const shown = all ? notes : notes.slice(0, NOTES_SHOWN);
  return (
    <div className="notes-group">
      <h3>
        {label} <span className="faint">({notes.length})</span>
      </h3>
      <ul className="notes">
        {shown.map((n, i) => (
          <NoteItem key={`${n.kind}-${n.metric}-${n.period_end}-${i}`} note={n} companyId={companyId} canReadFilings={canReadFilings} />
        ))}
      </ul>
      {notes.length > NOTES_SHOWN && (
        <button className="btn sm ghost" onClick={() => setAll(!all)}>
          {all ? "Show fewer" : `Show all ${notes.length}`}
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
  return (
    <li className={`note ${note.severity}`}>
      <span className={`tag sev-${note.severity}`}>{severityLabel(note.severity)}</span>
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

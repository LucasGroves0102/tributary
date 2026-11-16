import { useState } from "react";

import { dataNotes } from "../api";
import { useDirectory } from "../companies/directory";
import { SealLink } from "../components/Seal";
import { Empty } from "../components/States";
import { ago, day } from "../format";
import { href } from "../route";
import type { CoverageGap, DataNote, NoteFiling } from "../types";
import { coverageWords, REASON_WORDS, shownGaps } from "./coverage";
import { edgarUrl, explain, groupNotes, noteWhen, rankOf, ROLE_LABEL, severityLabel, summaryText } from "./notes";
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
        <span className="sub">what needs attention first, then the newest · gaps, restatements, splits, overlapping periods and freshness, from automated checks</span>
      </div>
      <Gate load={load} name={name} what="Data notes">
        {() => {
          if (load?.state !== "ok") return null;
          const { notes, checked_at } = load.data;
          const gaps = shownGaps(load.data.coverage ?? []);
          if (!notes.length) {
            return (
              <>
                {gaps.length > 0 && <Coverage gaps={gaps} name={name} token={token} />}
                <Empty>
                  Nothing to flag for {name}: {checked_at ? `the last check of its figures (${ago(checked_at)})` : "the checks"} found no gaps,
                  overlapping quarters or restatements{gaps.length ? "" : ", and the figures include every 10-K and 10-Q on EDGAR"}.
                </Empty>
              </>
            );
          }
          const summary = summaryText(load.data.summary);
          return (
            <>
              {summary && <p className="notes-summary">{summary}</p>}
              {gaps.length > 0 && <Coverage gaps={gaps} name={name} token={token} />}
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

/** What the nightly coverage sweep found missing from this company's figures or documents, and
 * whose gap it is (company/coverage.ts words them). Quiet: a reason in words, no colour. */
export function Coverage({ gaps, name, token }: { gaps: CoverageGap[]; name: string; token: string }) {
  const directory = useDirectory(token);
  return (
    <div className="notes-group coverage-notes">
      <h3>Coverage</h3>
      <ul className="coverage-list">
        {gaps.map((g) => {
          const { title, text } = coverageWords(g, name, (id) => directory.name(id) ?? "its parent");
          return (
            <li key={g.kind} className="coverage-gap">
              <div>
                <b>{title}</b>
                <span className="faint coverage-reason">{REASON_WORDS[g.reason] ?? g.reason}</span>
              </div>
              <p>{text}</p>
            </li>
          );
        })}
      </ul>
    </div>
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
  const { title, text, figures } = explain(note);
  const when = noteWhen(note);
  // 075: each figure a tag disagreement names, with a seal to the filing that states it
  const source = note.filings.find((f) => f.role === "source") ?? note.filings[0];
  const sealHref = source
    ? canReadFilings && /^10-[KQ]/.test(source.form ?? "")
      ? href({ view: "filing", companyId, accessionNo: source.accession_no, section: null })
      : edgarUrl(source)
    : null;
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
          {when && <span className="faint note-when">{when}</span>}
        </div>
        <p>{text}</p>
        {figures && (
          <ul className="note-figs">
            {figures.map((f) => (
              <li key={f.tag}>
                <span className="note-fig">{f.text}</span>
                {sealHref && source && (
                  <SealLink href={sealHref} label={`${f.text}: as the ${source.form ?? "filing"} states it`}
                    source={`${f.text} as ${f.tag}, in the ${source.form ?? "filing"} ${source.accession_no}${source.filed ? `, filed ${day(source.filed, true)}` : ""}`} />
                )}
                <span className="faint note-fig-tag">{f.tag}</span>
              </li>
            ))}
          </ul>
        )}
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

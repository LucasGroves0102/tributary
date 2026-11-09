/** The guided steps' bodies inside an analysis project. Each reuses the screens it comes from,
 * scoped to the project's company: the 10-K's Item 1 and the Segments tables (Business), the
 * chart builder's charts drawn by ChartView (Numbers), the company page's Earnings view
 * (Earnings), its "Since the last report" 8-Ks and the filing page's What changed view (What
 * changed), Compare (Peers). Data is read through the Query API as the viewer, like on
 * those screens; the project grants nothing. */

import { Fragment, useEffect, useId, useMemo, useRef, useState } from "react";

import { filingIndex, latestSection } from "../api";
import { stateOf } from "../workspaces/model";
import type { ChartState } from "../chart/model";
import { EarningsView } from "../company/Earnings";
import { EventsCard } from "../company/Events";
import { periodLabel } from "../company/changes";
import { Changes, type Change, type ChangedSection } from "../company/Filings";
import { Gate, Segments } from "../company/Tables";
import { TextKpisCard } from "../company/TextKpis";
import { useLoad } from "../company/useLoad";
import { day } from "../format";
import { Compare } from "../pages/Compare";
import { ChartView } from "../reports/Embeds";
import { href } from "../route";
import { embedMarker } from "../thesis/model";
import type { CompareState } from "../compare/state";
import type { IndexedFiling, ProjectState, SavedItem } from "../types";
import type { Saved } from "../workspaces/useSaved";
import { numbersCharts } from "./charts";
import { PinButton } from "./Pin";
import { MAX_NOTES, quoteBlock, type Passage } from "./project";

const everyMetric = () => true;
/** Item 1 is read up to this many characters (the reader has the rest) */
const ITEM1_CHARS = 20_000;
const ITEM1_SHOWN = 8;

export interface StepCommon {
  token: string;
  companyId: string;
  /** the company's name and ticker (or name again) */
  name: string;
  ticker: string;
  label: (companyId: string) => string;
}

/** Paragraphs of a section's text: blank-line or line breaks, short fragments (headings,
 * page furniture) joined to what follows. */
export function paragraphs(text: string): string[] {
  const out: string[] = [];
  let carry = "";
  for (const raw of text.split(/\n+/)) {
    const t = raw.replace(/\s+/g, " ").trim();
    if (!t) continue;
    if (t.length < 60 && !/[.!?:;]$/.test(t)) {
      carry = carry ? `${carry} ${t}` : t;
      continue;
    }
    out.push(carry ? `${carry} — ${t}` : t);
    carry = "";
  }
  if (carry) out.push(carry);
  return out;
}

// --- 1 Business ------------------------------------------------------------------------------

export function Business({ token, companyId, name, project, onNotes, notesStatus, canReadFilings }: StepCommon & {
  project: ProjectState;
  onNotes: (notes: string) => void;
  notesStatus: "saved" | "saving" | "error" | null;
  canReadFilings: boolean;
}) {
  const id = useId();
  const load = useLoad(canReadFilings ? `${companyId}|item1` : null, () => latestSection(token, companyId, "1", "10-K", ITEM1_CHARS));
  const [all, setAll] = useState(false);
  const [notes, setNotes] = useState(project.notes);
  const section = load?.state === "ok" ? load.data : null;
  const paras = useMemo(() => (section ? paragraphs(section.text) : []), [section]);
  const shown = all ? paras : paras.slice(0, ITEM1_SHOWN);
  // typing saves after a pause (and on leaving the field)
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    const t = window.setTimeout(() => onNotes(notes), 800);
    return () => window.clearTimeout(t);
  }, [notes]);

  return (
    <>
      <section className="card" aria-labelledby={`${id}-notes`}>
        <div className="card-h">
          <h3 id={`${id}-notes`}>How it makes money</h3>
          <span className="sub">your notes, saved with the project</span>
          <span className="grow" />
          <span className={notesStatus === "error" ? "err small" : "faint small"} role="status">
            {notesStatus === "saving" ? "Saving…" : notesStatus === "error" ? "Couldn't save the notes; they're kept here" : notesStatus === "saved" ? "Saved" : ""}
          </span>
        </div>
        <div className="card-b">
          <label className="visually-hidden" htmlFor={`${id}-text`}>How {name} makes money</label>
          <textarea id={`${id}-text`} className="field notes-text" value={notes} maxLength={MAX_NOTES} rows={5}
            placeholder={"Who pays, for what, and why they keep paying. Which segment earns the money; what it costs to win a customer.\n\nRead Item 1 below; the segments show where revenue comes from."}
            onChange={(e) => setNotes(e.target.value)} onBlur={() => notes !== project.notes && onNotes(notes)} />
          <p className="faint small">{notes.length.toLocaleString("en-US")} / {MAX_NOTES.toLocaleString("en-US")}</p>
        </div>
      </section>

      <section className="card" aria-labelledby={`${id}-item1`}>
        <div className="card-h">
          <h3 id={`${id}-item1`}>Business: 10-K Item 1</h3>
          <span className="sub">{section ? `10-K filed ${day(section.citation.filed, true)} · pin a paragraph to quote it in your thesis` : "the latest 10-K's description of the business"}</span>
          <span className="grow" />
          {section && (
            <a className="btn sm ghost" href={href({ view: "filing", companyId, accessionNo: section.citation.accession_no, section: section.section_key })}>
              Read the full Item 1
            </a>
          )}
        </div>
        {!canReadFilings ? (
          <p className="empty locked">Item 1 is filing text, which your plan doesn't include for {name}.</p>
        ) : (
          <Gate load={load} name={name} what="Filings">
            {() => !section || !paras.length ? (
              <p className="empty">No parsed 10-K Item 1 for {name} yet. Its filings are on the company page.</p>
            ) : (
              <div className="card-b item1">
                {shown.map((p, i) => (
                  <p key={i} className="pinnable">
                    {p}{" "}
                    <PinButton compact what="passage" make={async () => quoteBlock({
                      text: p, companyId, accessionNo: section.citation.accession_no, form: section.citation.form,
                      filed: section.citation.filed, period: section.citation.period_of_report ?? null,
                      section: section.title ? `Item 1. ${section.title.replace(/^item\s*1\.?\s*/i, "")}` : "Item 1",
                    })} />
                  </p>
                ))}
                {paras.length > ITEM1_SHOWN && (
                  <button className="btn sm ghost" aria-expanded={all} onClick={() => setAll((a) => !a)}>
                    {all ? "Show less" : `Show all ${paras.length} paragraphs`}
                  </button>
                )}
                {section.truncated && <p className="faint small">The first {ITEM1_CHARS.toLocaleString("en-US")} characters; the reader has the rest.</p>}
              </div>
            )}
          </Gate>
        )}
      </section>

      <div className="step-sub">
        <h3>Segments</h3>
        <p className="faint small">Revenue and operating income by business segment and geography, as reported in the filings' XBRL.</p>
      </div>
      <Segments token={token} companyId={companyId} name={name} />
    </>
  );
}

// --- 2 Numbers ---------------------------------------------------------------------------------

export function Numbers({ token, companyId, name, ticker, label, saved, projectId, charts, onOpenChart, onSaved, canReadFilings = false }: StepCommon & {
  /** the filings tier: backlog and orders from the filings' text (F8b) */
  canReadFilings?: boolean;
  saved: Saved;
  projectId: string;
  /** the project's saved charts */
  charts: SavedItem[];
  onOpenChart: (s: ChartState) => void;
  /** a chart was saved into the project */
  onSaved: () => void;
}) {
  const sets = useMemo(() => numbersCharts(companyId, ticker), [companyId, ticker]);
  const [done, setDone] = useState<Record<string, "saving" | "saved" | string>>({});
  const savedNames = new Set(charts.map((c) => c.name));
  const save = async (key: string, chart: ChartState) => {
    setDone((d) => ({ ...d, [key]: "saving" }));
    try {
      await saved.create("chart", chart.title, stateOf({ kind: "chart", chart }), { workspaceId: projectId });
      setDone((d) => ({ ...d, [key]: "saved" }));
      onSaved();
    } catch {
      setDone((d) => ({ ...d, [key]: "Couldn't save it. Try again." }));
    }
  };
  return (
    <>
      {sets.map((s) => {
        const state = done[s.key];
        const already = state === "saved" || savedNames.has(s.chart.title);
        // F8b: after the backlog proxy, what the filings state in words
        const text = s.key === "backlog" ? (
          <TextKpisCard token={token} companyId={companyId} name={name} canRead={canReadFilings} compact />
        ) : null;
        return (
          <Fragment key={s.key}>
          <section className="card numbers-chart" aria-label={s.chart.title}>
            <div className="card-b">
              <ChartView token={token} chart={s.chart} name={s.chart.title} label={label} hasTtm={everyMetric} />
              <p className="small muted read-hint">{s.read}</p>
              <div className="row wrap chart-actions">
                <button className="btn sm ghost" onClick={() => onOpenChart(s.chart)}>Open in Chart builder</button>
                <button className="btn sm" disabled={already || state === "saving"} onClick={() => void save(s.key, s.chart)}
                  title="Save this chart in the project (it can be embedded in the thesis)">
                  {already ? "✓ Saved to project" : state === "saving" ? "Saving…" : "Save to project"}
                </button>
                <PinButton what="chart" make={async () => {
                  const item = await saved.create("chart", s.chart.title, stateOf({ kind: "chart", chart: s.chart }), { workspaceId: projectId });
                  onSaved();
                  return embedMarker(item.item_id);
                }} />
                {state && state !== "saving" && state !== "saved" && <span className="err small" role="alert">{state}</span>}
              </div>
            </div>
          </section>
          {text}
          </Fragment>
        );
      })}
      <p className="faint small">
        Up to ten fiscal years of {name}, one point per year (flows are the year's total, balances the year end), then the
        latest quarter when the year isn't over, marked * and drawn lighter (the trailing twelve months for flows, the quarter
        end for balances); receivables vs revenue is quarterly. Outflows (capex, buybacks, dividends) are drawn below zero, as
        the cash flow statement prints them. Values link to their filings. Charts you save here, or from the Chart builder into this project,
        show under Charts in the project's workspace.
      </p>
    </>
  );
}

// --- 3 Earnings --------------------------------------------------------------------------------

/** The quarter's earnings release beside its figures, its outlook and adjustments (pinnable). */
export function EarningsStep({ token, companyId, name, canReadFilings, canReadFinancials }: StepCommon & {
  canReadFilings: boolean;
  canReadFinancials: boolean;
}) {
  return <EarningsView token={token} companyId={companyId} name={name} canReadFilings={canReadFilings} canReadFinancials={canReadFinancials} />;
}

// --- 4 What changed ----------------------------------------------------------------------------

const changePassage = (companyId: string, c: Change, s: ChangedSection): Passage | null => {
  const removed = c.kind === "removed";
  const cite = removed ? c.prior_citation : c.citation;
  const text = removed ? c.prior_text : c.text;
  if (!cite || !text) return null;
  // F3: a 10-Q passage says which period it describes ("MD&A (6 months, year to date)")
  const period = periodLabel(c.period);
  const section = s.title ?? `Item ${s.section_key}`;
  return {
    text, companyId, accessionNo: cite.accession_no, form: cite.form, filed: cite.filed,
    period: cite.period_of_report, section: period ? `${section} (${period})` : section, change: c.kind,
  };
};

export function WhatChanged({ token, companyId, name, canReadFilings }: StepCommon & { canReadFilings: boolean }) {
  const index = useLoad(`${companyId}|periodic`, () => filingIndex(token, companyId, { forms: ["10-K", "10-Q"], limit: 12 }));
  const rows = index?.state === "ok" ? index.data.filings : [];
  const latest = (form: string) => rows.find((f) => f.form === form && f.sections_available) ?? null;
  const choices = [latest("10-Q"), latest("10-K")].filter((f): f is IndexedFiling => f !== null)
    .sort((a, b) => b.filed.localeCompare(a.filed));
  const [pick, setPick] = useState<string | null>(null);
  const shown = choices.find((f) => f.accession_no === pick) ?? choices[0] ?? null;
  if (!canReadFilings) return <p className="empty locked card">What changed compares filing text, which your plan doesn't include for {name}.</p>;
  return (
    <>
    <EventsCard token={token} companyId={companyId} name={name} />
    <section className="card">
      <div className="card-h wrap">
        <h3>Risk Factors and MD&amp;A, paragraph by paragraph</h3>
        {choices.length > 1 && (
          <span className="seg" role="group" aria-label="Filing">
            {choices.map((f) => (
              <button key={f.accession_no} className={f === shown ? "on" : undefined} aria-pressed={f === shown} onClick={() => setPick(f.accession_no)}>
                Latest {f.form} · {day(f.filed, true)}
              </button>
            ))}
          </span>
        )}
        <span className="sub">pin a paragraph to quote it in your thesis</span>
      </div>
      <Gate load={index} name={name} what="Filings">
        {() => !shown ? (
          <p className="empty">No parsed 10-K or 10-Q for {name} yet, so there's nothing to compare.</p>
        ) : (
          <Changes key={shown.accession_no} token={token} companyId={companyId} accessionNo={shown.accession_no} name={name}
            form={shown.form} pin={(c, s) => {
              const p = changePassage(companyId, c, s);
              return p ? <PinButton compact what="passage" make={async () => quoteBlock(p)} /> : null;
            }} />
        )}
      </Gate>
    </section>
    </>
  );
}

// --- 5 Peers ---------------------------------------------------------------------------------

export function PeersStep({ token, companyId, project, onPeers, onChart, saved, projectId, onSaved }: StepCommon & {
  project: ProjectState;
  onPeers: (peers: string[]) => void;
  onChart: (companyIds: string[], metric: string, transform: "ttm" | "q" | "yoy") => void;
  saved: Saved;
  projectId: string;
  onSaved: () => void;
}) {
  const [cols, setCols] = useState<string[] | undefined>(undefined);
  const compare: CompareState = { target: companyId, peers: project.peers, ...(cols ? { cols } : {}) };
  const [state, setState] = useState<"saving" | "saved" | "error" | null>(null);
  const saveComps = async () => {
    setState("saving");
    try {
      await saved.create("comps", "Comps", stateOf({ kind: "comps", compare }), { workspaceId: projectId });
      setState("saved");
      onSaved();
    } catch {
      setState("error");
    }
  };
  return (
    <Compare token={token} compare={compare} onChart={onChart}
      embedded={{
        onChange: (next) => {
          if (JSON.stringify(next.peers ?? []) !== JSON.stringify(project.peers)) onPeers(next.peers ?? []);
          setCols(next.cols);
        },
      }}
      saveBar={
        <span className="row wrap">
          <button className="btn" onClick={() => void saveComps()} disabled={state === "saving"}
            title="Save this comps table in the project (it can be embedded in the thesis)">
            {state === "saved" ? "✓ Saved to project" : state === "saving" ? "Saving…" : "Save to project"}
          </button>
          <PinButton what="comps table" make={async () => {
            const item = await saved.create("comps", "Comps", stateOf({ kind: "comps", compare }), { workspaceId: projectId });
            onSaved();
            return embedMarker(item.item_id);
          }} />
          {state === "error" && <span className="err small" role="alert">Couldn't save it. Try again.</span>}
        </span>
      } />
  );
}

import { useId } from "react";

import { companyEvents } from "../api";
import { PinButton } from "../analysis/Pin";
import { quoteBlock } from "../analysis/project";
import { day } from "../format";
import { sectionName } from "../reader/sections";
import { href } from "../route";
import type { EventsResponse, FilingEvent } from "../types";
import { ANNOUNCED_ITEM, ANNOUNCED_LABEL, eventLabels, itemLabel, METRIC_LABEL, splitEvents, staleFlags, windowLine } from "./events";
import { Gate } from "./Tables";
import { type Load, useLoad } from "./useLoad";

/** "Since the last report": the 8-Ks a company filed after its latest 10-Q/10-K period end
 * (Query API /events), newest first, each with its items, the 8-K's own opening words (pinnable in
 * an analysis) and where to read it; and the figures as of that period end a later event may have
 * changed (tester 1's acquisition after the quarter end): flagged with the event, never recomputed.
 * On the company's Overview and at the top of the What changed step. `load`: the Overview's own
 * request, so the page asks once for the card and its key-stat flags. */
export function EventsCard({ token, companyId, name, load: given }: {
  token: string;
  companyId: string;
  name: string;
  load?: Load<EventsResponse | null> | null;
}) {
  const id = useId();
  const own = useLoad(given === undefined ? `${companyId}|events` : null, () => companyEvents(token, companyId));
  const load = given === undefined ? own : given;
  const data = load?.state === "ok" ? load.data : null;
  return (
    <section className="card events-card" aria-labelledby={id}>
      <div className="card-h wrap">
        <h2 id={id}>Since the last report</h2>
        <span className="sub">{data ? windowLine(data) : "8-Ks filed after the latest 10-Q or 10-K period end"}</span>
      </div>
      <Gate load={load} name={name} what="Filings">
        {() => data === null ? (
          <p className="empty">8-Ks since the last report aren't available here yet. The company's Filings tab lists every 8-K.</p>
        ) : (
          <EventsBody companyId={companyId} data={data} />
        )}
      </Gate>
    </section>
  );
}

function EventsBody({ companyId, data }: { companyId: string; data: EventsResponse }) {
  const flags = staleFlags(data);
  const { material, routine } = splitEvents(data);
  const asOf = flags[0]?.asOf;
  return (
    <div className="card-b">
      {flags.length > 0 && (
        <div className="stale-box" role="note">
          <b>⚑ Figures as of {asOf ? day(asOf, true) : "the last report"} that a later 8-K may have changed</b>
          <ul>
            {flags.map((f) => (
              <li key={f.metric}><b>{f.label}</b>: before {f.short}</li>
            ))}
          </ul>
          <p className="small muted">
            Tributary doesn't compute a figure after the event (no pro forma): the 8-K says what happened, the next 10-Q or 10-K
            what the balance sheet became.
          </p>
        </div>
      )}
      {material.length === 0 ? (
        <p className="empty">No 8-K with a material item since {day(data.since, true)}.</p>
      ) : (
        <ol className="events">
          {material.map((e) => <EventRow key={e.accession_no} companyId={companyId} e={e} />)}
        </ol>
      )}
      {routine.length > 0 && (
        <details className="routine">
          <summary className="small muted">
            {routine.length} routine filing{routine.length === 1 ? "" : "s"} (shareholder votes, nominations, exhibits alone)
          </summary>
          <ol className="events">
            {routine.map((e) => <EventRow key={e.accession_no} companyId={companyId} e={e} />)}
          </ol>
        </details>
      )}
      {data.more && <p className="faint small">The newest 25 are shown; the Filings tab lists every 8-K.</p>}
    </div>
  );
}

function EventRow({ companyId, e }: { companyId: string; e: FilingEvent }) {
  const ex = e.excerpt;
  const cite = ex?.citation;
  const edgar = e.document_url ?? e.filing_url;
  const affects = e.affects.map((m) => METRIC_LABEL[m] ?? m);
  return (
    <li className="event">
      <div className="row wrap event-head">
        <span className="event-date">{day(e.filed, true)}</span>
        <span className="tag k">{e.form}</span>
        {eventLabels(e).map((l) => <span key={l} className="tag">{l}</span>)}
        {affects.length > 0 && (
          <span className="tag warn" title={`An event after the period end: ${affects.join(", ")} as of that date may have changed${e.announced?.length ? " (read from the 8-K's announcement)" : ""}`}>
            may change {affects.join(", ").toLowerCase()}
          </span>
        )}
        {e.event_date !== e.filed && <span className="faint small">event {day(e.event_date, true)}</span>}
      </div>
      {(e.announced ?? []).map((a) => (
        <p key={a.kind} className="event-announced" role="note">
          <b>⚑ {capital(ANNOUNCED_LABEL[a.kind] ?? a.kind)}</b>, read from its Item {a.item} announcement
          {e.items.some((i) => i.item === ANNOUNCED_ITEM[a.kind]) ? "" : ` (it has no Item ${ANNOUNCED_ITEM[a.kind]})`}: the words{" "}
          <mark>“{a.words}”</mark> in{" "}
          <a href={href({ view: "filing", companyId, accessionNo: e.accession_no, section: a.section_key, ...(a.citation.char_start !== undefined ? { at: a.citation.char_start } : {}) })}>
            {sectionName(a.section_key)}
          </a>
          . <span className="faint">“{a.sentence}”</span>
        </p>
      ))}
      {ex && cite?.section_key ? (
        <p className="pinnable event-excerpt">
          <span className="faint small">{sectionName(ex.item)}: </span>“{ex.text}”{" "}
          <PinButton compact what="passage" make={async () => quoteBlock({
            text: ex.text, companyId, accessionNo: e.accession_no, form: e.form, filed: e.filed, period: null,
            section: `Item ${ex.item} ${itemLabel(e.items.find((i) => i.item === ex.item) ?? { item: ex.item, title: ex.item, kind: "" })}, event of ${e.event_date}`,
          })} />
        </p>
      ) : !e.sections_available ? (
        <p className="faint small">Not stored here: {e.items.map((i) => `Item ${i.item} ${i.title}`).join("; ")}. Read it on EDGAR.</p>
      ) : null}
      <div className="row wrap event-links">
        {ex && cite?.section_key && (
          <a className="reader-link" href={href({ view: "filing", companyId, accessionNo: e.accession_no, section: cite.section_key, ...(cite.char_start !== undefined ? { at: cite.char_start } : {}) })}>
            Read the 8-K
          </a>
        )}
        {e.release && (
          <a className="reader-link" href={href({ view: "filing", companyId, accessionNo: e.accession_no, section: e.release.section_key })}>
            Read the press release
          </a>
        )}
        {edgar && (
          <a className="edgar-link" href={edgar} target="_blank" rel="noopener noreferrer" title={`The 8-K on EDGAR, sec.gov (${e.accession_no})`}>
            EDGAR ↗
          </a>
        )}
        {e.items_from === "parsed" && (
          <span className="faint small" title="The SEC's index lists a filing's items overnight; until then they come from the 8-K's own headings">
            items from the 8-K itself
          </span>
        )}
      </div>
    </li>
  );
}

const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

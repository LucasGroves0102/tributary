import type { Readable } from "../access";
import { recentDataNotes } from "../api";
import { NoteItem } from "../company/DataNotes";
import { useLoad } from "../company/useLoad";
import { ErrorPanel, Skeleton } from "../components/States";
import { ago } from "../format";
import { href } from "../route";

export const HOME_NOTES = 6;

/** As many as the API takes for one request; a longer watchlist reads its first ones. */
export const NOTE_COMPANIES = 50;

/** Home: the newest data notes (restatements, filings not yet in the figures, gaps...) for the
 * user's own companies: with the catalog open, the ones they watch or analyse, since every company
 * is readable and none is "theirs" until chosen; while it's closed, the ones they can read. */
export function RecentNotes({ token, name, filingsFor, catalogOpen = false, companies = null }: {
  token: string;
  /** "Nvidia (NVDA)" (companies/directory.ts) */
  name: (companyId: string) => string;
  /** company ids whose filings the user can read (links open the reader there) */
  filingsFor: Readable;
  /** every company is readable, so the notes are those of `companies` */
  catalogOpen?: boolean;
  /** the companies the user watches or analyses (null while they load) */
  companies?: string[] | null;
}) {
  const ids = catalogOpen ? companies?.slice(0, NOTE_COMPANIES) ?? null : undefined;
  // with the catalog open, wait for the list; an empty one asks for nothing
  const key = ids === null || ids?.length === 0 ? null : `${token}|${ids?.join() ?? ""}`;
  const load = useLoad(key, () => recentDataNotes(token, HOME_NOTES, ids ?? undefined));
  if (catalogOpen && ids?.length === 0) {
    return (
      <section className="card">
        <div className="card-h"><h2>Recent data notes for your companies</h2></div>
        <p className="empty">Watch a company or start an analysis, and its restatements, gaps and late XBRL show up here.</p>
      </section>
    );
  }
  return (
    <section className="card">
      <div className="card-h">
        <h2>Recent data notes for your companies</h2>
        <span className="sub">
          restatements, filings not yet in the figures, gaps{load?.state === "ok" && load.data.checked_at ? ` · checks last ran ${ago(load.data.checked_at)}` : ""}
        </span>
      </div>
      {!load || load.state === "loading" ? (
        <Skeleton lines={3} label="Loading data notes" />
      ) : load.state === "error" ? (
        <ErrorPanel what="data notes" detail={load.message} onRetry={load.retry} />
      ) : load.state === "denied" ? null : load.data.notes.length === 0 ? (
        <p className="empty">
          {load.data.companies
            ? `No data notes for the ${load.data.companies} ${catalogOpen ? "companies you watch or analyse" : "companies whose financials you can read"}: no gaps, restatements or late XBRL found.`
            : "Data notes appear here for companies whose financials you can read."}
        </p>
      ) : (
        <ul className="notes">
          {load.data.notes.map((n, i) => (
            <NoteItem
              key={`${n.company_id}-${n.kind}-${n.metric}-${n.period_end}-${i}`}
              note={n}
              companyId={n.company_id}
              company={name(n.company_id)}
              canReadFilings={filingsFor.has(n.company_id)}
            />
          ))}
        </ul>
      )}
      {load?.state === "ok" && load.data.notes.length > 0 && (
        <div className="foot">
          The newest notes first; each company's full list is on its{" "}
          <a href={href({ view: "company", companyId: load.data.notes[0]!.company_id, tab: "notes" })}>Data notes</a> tab.
        </div>
      )}
    </section>
  );
}

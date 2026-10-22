import { recentDataNotes } from "../api";
import { NoteItem } from "../company/DataNotes";
import { useLoad } from "../company/useLoad";
import { ErrorPanel, Skeleton } from "../components/States";
import { ago } from "../format";
import { href } from "../route";

export const HOME_NOTES = 6;

/** Home: the newest data notes (restatements, filings not yet in the figures, gaps...) across
 * the companies whose financials the user can read. */
export function RecentNotes({ token, name, filingsFor }: {
  token: string;
  name: (companyId: string) => string | null;
  /** company ids whose filings the user can read (links open the reader there) */
  filingsFor: Set<string>;
}) {
  const load = useLoad(token, () => recentDataNotes(token, HOME_NOTES));
  return (
    <section className="card">
      <div className="card-h">
        <h2>Recent data notes for your companies</h2>
        <span className="sub">
          restatements, filings not yet in the figures, gaps{load?.state === "ok" && load.data.checked_at ? ` · checked ${ago(load.data.checked_at)}` : ""}
        </span>
      </div>
      {!load || load.state === "loading" ? (
        <Skeleton lines={3} label="Loading data notes" />
      ) : load.state === "error" ? (
        <ErrorPanel what="data notes" detail={`The Query API said: ${load.message}.`} onRetry={load.retry} />
      ) : load.state === "denied" ? null : load.data.notes.length === 0 ? (
        <p className="empty">
          {load.data.companies
            ? `No data notes for the ${load.data.companies} companies whose financials you can read: no gaps, restatements or late XBRL found.`
            : "Data notes appear here for companies whose financials you can read."}
        </p>
      ) : (
        <ul className="notes">
          {load.data.notes.map((n, i) => (
            <NoteItem
              key={`${n.company_id}-${n.kind}-${n.metric}-${n.period_end}-${i}`}
              note={n}
              companyId={n.company_id}
              company={name(n.company_id) ?? n.company_id}
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

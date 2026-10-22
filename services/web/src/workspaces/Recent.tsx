import { ago, day } from "../format";
import { href } from "../route";
import type { RecentWork } from "../types";
import { KIND_LABELS } from "./model";

const ROWS = 8;

type Row =
  | { at: string; type: "item"; item: RecentWork["items"][number] }
  | { at: string; type: "filing"; filing: RecentWork["filings"][number] };

/** Saved items the user opened and filings they read, newest first. */
export function recentRows(recent: RecentWork, limit = ROWS): Row[] {
  const rows: Row[] = [
    ...recent.items.map((item): Row => ({ at: item.opened_at ?? item.updated_at, type: "item", item })),
    ...recent.filings.map((filing): Row => ({ at: filing.read_at, type: "filing", filing })),
  ];
  return rows.sort((a, b) => Date.parse(b.at) - Date.parse(a.at)).slice(0, limit);
}

/** Home's "Continue where you left off". */
export function Recent({ recent, onOpen }: { recent: RecentWork | null; onOpen: (itemId: string) => void }) {
  if (recent === null) return <p className="empty">Loading your recent work…</p>;
  const rows = recentRows(recent);
  if (!rows.length) {
    return (
      <p className="empty">
        Charts, comps and screens you open from a workspace, and filings you read, show up here. Save
        one with the Save button on the Chart builder, Compare or Companies.
      </p>
    );
  }
  return (
    <table className="t recent">
      <tbody>
        {rows.map((r) =>
          r.type === "item" ? (
            <tr key={r.item.item_id} className="clk" onClick={() => onOpen(r.item.item_id)}>
              <td className="kind">
                <span className={`tag ${r.item.kind}`}>{KIND_LABELS[r.item.kind]}</span>
              </td>
              <td>
                <button className="link" onClick={(e) => { e.stopPropagation(); onOpen(r.item.item_id); }}>
                  <b>{r.item.name}</b>
                </button>{" "}
                <span className="muted">· in workspace “{r.item.workspace_name}”</span>
              </td>
              <td className="faint n">opened {ago(r.at)}</td>
            </tr>
          ) : (
            <tr key={`${r.filing.company_id}-${r.filing.accession_no}`}>
              <td className="kind">
                <span className="tag">Filing</span>
              </td>
              <td>
                <a
                  href={href({
                    view: "filing",
                    companyId: r.filing.company_id,
                    accessionNo: r.filing.accession_no,
                    section: r.filing.section_key,
                  })}
                >
                  <b>
                    {r.filing.name ?? r.filing.company_id} {r.filing.form ?? "filing"}
                  </b>
                </a>{" "}
                <span className="muted">
                  {r.filing.period_end && `· period ending ${day(r.filing.period_end, true)}`}
                  {r.filing.section_key && ` · read to section ${r.filing.section_key}`}
                </span>
              </td>
              <td className="faint n">read {ago(r.at)}</td>
            </tr>
          ),
        )}
      </tbody>
    </table>
  );
}

import { useRef } from "react";

import { financials, latestSection, recap as fetchRecap } from "../api";
import { LayerChart } from "../chart/LayerChart";
import { financialSeries } from "../chart/series";
import { day } from "../format";
import { href } from "../route";
import type { ScreenerRow, Tier } from "../types";
import { keyStats, recapAccession, recapRows } from "./data";
import { Gate } from "./Tables";
import { useLoad } from "./useLoad";

const EXCERPT_CHARS = 1500;

/** Key stats, the latest quarter vs the prior and year-ago quarters, the business description
 * (10-K Item 1) and quarterly revenue. */
export function Overview({
  token,
  companyId,
  name,
  row,
  access,
}: {
  token: string;
  companyId: string;
  name: string;
  row: ScreenerRow;
  access: Tier[];
}) {
  const canFin = access.includes("financials");
  return (
    <>
      <section className="card">
        <div className="card-h">
          <h2>Key stats</h2>
          <span className="sub">latest reported periods · USD · hover for the source filing</span>
        </div>
        {canFin ? (
          <div className="kv">
            {keyStats(row).map((s) => (
              <div key={s.label} title={s.title}>
                <div className="k">{s.label}</div>
                <div className="v">{s.text}</div>
                <div className="p">{s.period}</div>
              </div>
            ))}
          </div>
        ) : (
          <p className="empty locked">Financials for {name} aren't included in your access; its filings and metadata are public.</p>
        )}
      </section>
      <div className="grid2">
        <Recap token={token} companyId={companyId} name={name} accession={canFin ? recapAccession(row) : null} canFin={canFin} />
        <section className="card">
          <Business token={token} companyId={companyId} name={name} canRead={access.includes("filings")} />
          {canFin && <Revenue token={token} companyId={companyId} />}
        </section>
      </div>
    </>
  );
}

function Recap({ token, companyId, name, accession, canFin }: { token: string; companyId: string; name: string; accession: string | null; canFin: boolean }) {
  const load = useLoad(accession, () => fetchRecap(token, accession!));
  const data = load?.state === "ok" ? load.data : null;
  const table = data ? recapRows(data) : null;
  return (
    <section className="card">
      <div className="card-h">
        <h2>Latest quarter</h2>
        <span className="sub">
          {data ? `${data.form} filed ${day(data.filed, true)} · vs prior quarter and year-ago` : "vs prior quarter and year-ago"} ·
          no consensus estimates in Tributary
        </span>
      </div>
      {!canFin ? (
        <p className="empty locked">The recap needs financials for {name}.</p>
      ) : !accession ? (
        <p className="empty">No 10-Q or 10-K values for {name} yet.</p>
      ) : (
        <Gate load={load} name={name} what="Financials">
          {() => (
            <>
              <div className="tw">
                <table className="t">
                  <thead>
                    <tr>
                      <th className="first">Metric</th>
                      <th className="n">{table!.labels[0]}</th>
                      <th className="n">{table!.labels[1]}</th>
                      <th className="n">QoQ</th>
                      <th className="n">{table!.labels[2]}</th>
                      <th className="n">YoY</th>
                    </tr>
                  </thead>
                  <tbody>
                    {table!.rows.map((r) => (
                      <tr key={r.label} title={r.title}>
                        <td className="first">{r.label}</td>
                        <td className="n"><b>{r.current}</b></td>
                        <td className="n">{r.prior}</td>
                        <td className={`n ${!r.qoq ? "faint" : r.qoq.sign >= 0 ? "pos" : "neg"}`}>{r.qoq?.text ?? "—"}</td>
                        <td className="n">{r.yearAgo}</td>
                        <td className={`n ${!r.yoy ? "faint" : r.yoy.sign >= 0 ? "pos" : "neg"}`}>{r.yoy?.text ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {data!.flags && data!.flags.length > 0 && (
                <div className="card-b">
                  <b>Flagged in the filing text</b>
                  <ul className="flags">
                    {data!.flags.map((f, i) => (
                      <li key={i}>
                        {f.title ?? f.flag}: “{f.snippet}”{" "}
                        <a href={href({ view: "filing", companyId, accessionNo: f.citation.accession_no, section: f.citation.section_key })}>
                          {f.section_key}
                        </a>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <div className="foot">
                {data!.flags_unavailable ? "One-time-item flags from the filing text need the filings tier. " : ""}
                Margins change in basis points. Values as currently reported; a restated value cites the restating filing.
              </div>
            </>
          )}
        </Gate>
      )}
    </section>
  );
}

/** The first part of the latest 10-K's Item 1, cut at a sentence end. */
export function excerpt(text: string, max = 900): string {
  const t = text.replace(/\s+\n/g, "\n").trim();
  if (t.length <= max) return t;
  const cut = t.slice(0, max);
  const end = cut.lastIndexOf(". ");
  return (end > max / 2 ? cut.slice(0, end + 1) : cut) + " …";
}

function Business({ token, companyId, name, canRead }: { token: string; companyId: string; name: string; canRead: boolean }) {
  const load = useLoad(canRead ? companyId : null, () => latestSection(token, companyId, "1", "10-K", EXCERPT_CHARS));
  const section = load?.state === "ok" ? load.data : null;
  return (
    <>
      <div className="card-h">
        <h2>Business</h2>
        <span className="sub">{section ? `10-K filed ${day(section.citation.filed, true)} · Item 1 excerpt` : "10-K Item 1 excerpt"}</span>
      </div>
      {!canRead ? (
        <p className="empty locked">The description comes from the 10-K's text, which needs the pro plan and a grant for {name}.</p>
      ) : (
        <Gate load={load} name={name} what="Filings">
          {() =>
            section ? (
              <div className="card-b">
                <p className="excerpt">{excerpt(section.text)}</p>
                <a href={href({ view: "filing", companyId, accessionNo: section.citation.accession_no, section: section.section_key })}>
                  Read Item 1
                </a>
              </div>
            ) : (
              <p className="empty">No parsed 10-K Item 1 for {name} yet.</p>
            )
          }
        </Gate>
      )}
    </>
  );
}

/** Quarterly revenue (bars) and operating margin (line, right axis) on fiscal quarters, in the
 * chart builder's chart: hover shows each value's filing. */
function Revenue({ token, companyId }: { token: string; companyId: string }) {
  const svg = useRef<SVGSVGElement>(null);
  const load = useLoad(companyId, () => financials(token, companyId, 12, ["revenue", "operating_margin"]));
  const series = load?.state === "ok"
    ? [
        financialSeries(load.data.rows, "revenue", { id: 1, name: "Revenue", color: 0, type: "bar", axis: "L" }),
        financialSeries(load.data.rows, "operating_margin", { id: 2, name: "Operating margin", color: 1, type: "line", axis: "R" }),
      ].filter((s) => s.points.length)
    : [];
  return (
    <>
      <div className="card-h">
        <h2>Revenue and operating margin</h2>
        <span className="sub">fiscal quarters, last 12 · hover for each value's filing</span>
        <span className="grow" />
        <a className="btn sm ghost" href={href({ view: "company", companyId, tab: "statements" })}>Statements</a>
      </div>
      {series.length ? (
        <div className="card-b">
          <LayerChart series={series} title="" footnote={[]} svgRef={svg} />
        </div>
      ) : (
        <p className="empty">{!load || load.state === "loading" ? "Loading…" : "No revenue to chart."}</p>
      )}
    </>
  );
}

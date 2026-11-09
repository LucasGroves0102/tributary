import { useRef } from "react";

import { companyEvents, financials, latestSection, recap as fetchRecap } from "../api";
import { LayerChart } from "../chart/LayerChart";
import { financialSeries } from "../chart/series";
import { day } from "../format";
import { go, href } from "../route";
import type { ScreenerRow, Tier } from "../types";
import { BANK_NOT_SHOWN, header, isBankRow, keyStats, recapAccession } from "./data";
import { ErrorPanel, sentence, Skeleton } from "../components/States";
import { EventsCard } from "./Events";
import { keyStatFlag, staleFlags } from "./events";
import { RecapTable } from "./RecapTable";
import { TextKpisCard } from "./TextKpis";
import { Gate } from "./Tables";
import { useLoad } from "./useLoad";

const EXCERPT_CHARS = 1500;

/** Key stats (a balance a later 8-K may have changed is flagged, never recomputed), what the
 * company filed since its last report, the latest quarter vs the prior and year-ago quarters, the
 * business description (10-K Item 1), quarterly revenue, and backlog and orders as the filings
 * state them in words (F8b). */
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
  const canRead = access.includes("filings");
  const events = useLoad(canRead ? `${companyId}|events` : null, () => companyEvents(token, companyId));
  const flags = staleFlags(events?.state === "ok" ? events.data : null);
  return (
    <>
      <section className="card">
        <div className="card-h">
          <h2>Key stats</h2>
          <span className="sub">the latest period of each figure · {header(row).currency ?? "USD"} · hover for the source filing, or for why a figure reads n/m</span>
        </div>
        {canFin ? (
          <div className="kv stats">
            {keyStats(row).map((s) => {
              const flag = keyStatFlag(s.key, s.periodEnd, flags);
              return (
                <div key={s.label} title={flag ? `${s.title}\n\n${flag.text}` : s.title} className={s.old ? "old" : undefined}>
                  <div className="k">{s.label}</div>
                  <div className="v">{s.text}</div>
                  <div className="p">{s.period}</div>
                  {flag && <div className="p stale-flag">⚑ before {flag.short}</div>}
                </div>
              );
            })}
          </div>
        ) : (
          <p className="empty locked">Financials for {name} aren't included in your access; its filings and metadata are public.</p>
        )}
        {canFin && isBankRow(row) && "net_interest_income_ttm" in row.values && (
          <p className="foot bank-note">A bank's own figures. {BANK_NOT_SHOWN}</p>
        )}
      </section>
      {canRead && <EventsCard token={token} companyId={companyId} name={name} load={events} />}
      <div className="grid2">
        <Recap token={token} companyId={companyId} name={name} accession={canFin ? recapAccession(row) : null} canFin={canFin} />
        <section className="card">
          <Business token={token} companyId={companyId} name={name} canRead={access.includes("filings")} />
        </section>
      </div>
      {canFin && (
        <section className="card">
          <Revenue token={token} companyId={companyId} />
        </section>
      )}
      <TextKpisCard token={token} companyId={companyId} name={name} canRead={canRead} />
    </>
  );
}

function Recap({ token, companyId, name, accession, canFin }: { token: string; companyId: string; name: string; accession: string | null; canFin: boolean }) {
  const load = useLoad(accession, () => fetchRecap(token, accession!));
  const data = load?.state === "ok" ? load.data : null;
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
        <p className="empty">No 10-Q or 10-K values for {name} yet. Its Filings tab lists what it has filed.</p>
      ) : (
        <Gate load={load} name={name} what="Financials">
          {() => (
            <>
              <RecapTable data={data!} label="Latest quarter" />
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
                {data!.flags_unavailable ? `${sentence(data!.flags_unavailable)} ` : ""}
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
              <p className="empty">No parsed 10-K Item 1 for {name} yet. Its latest 10-K is on the Filings tab.</p>
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
        financialSeries(load.data.rows, "revenue", { id: 1, name: "Revenue", color: 0, type: "bar", axis: "L" }, companyId),
        financialSeries(load.data.rows, "operating_margin", { id: 2, name: "Operating margin", color: 1, type: "line", axis: "R" }, companyId),
      ].filter((s) => s.points.length)
    : [];
  // the heading says what's drawn: a bank reports no operating margin, so it gets revenue alone
  const heading = series.length === 1 ? series[0]!.name : "Revenue and operating margin";
  const without = series.length === 1 ? (series[0]!.id === 1 ? " · no operating margin reported" : " · no revenue reported") : "";
  return (
    <>
      <div className="card-h">
        <h2>{heading}</h2>
        <span className="sub">fiscal quarters, last 12{without} · hover for each value's filing</span>
        <span className="grow" />
        <a className="btn sm ghost" href={href({ view: "company", companyId, tab: "statements" })}>Statements</a>
      </div>
      {series.length ? (
        <div className="card-b">
          <LayerChart series={series} title="" footnote={[]} svgRef={svg}
            onOpen={(src) => go({ view: "filing", companyId, accessionNo: src.accessionNo, section: null })} />
        </div>
      ) : (
        !load || load.state === "loading" ? (
          <Skeleton chart label="Loading revenue" />
        ) : load.state === "error" ? (
          <ErrorPanel what="revenue" onRetry={load.retry} />
        ) : (
          <p className="empty">No quarterly revenue to chart. Statements shows what this company reports.</p>
        )
      )}
    </>
  );
}

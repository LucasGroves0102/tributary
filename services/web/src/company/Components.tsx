import { components as fetchComponents } from "../api";
import { ErrorPanel, Skeleton } from "../components/States";
import { TableScroll } from "../components/TableScroll";
import { day } from "../format";
import { href } from "../route";
import type { ComponentFact, ComponentFigure, ComponentPart, Components } from "../types";
import { factSource, factWhy, figureText, headline, HOW, LEFT_OUT, partName, roleMark, tagText } from "./components";
import { statementSection } from "./data";
import { useLoad } from "./useLoad";

const ACCESSION = /^\d{10}-\d{2}-\d{6}$/;

/** A filing as two links: the reader at its statements, and EDGAR. */
function Filing({ companyId, accessionNo, form, filed, url }: { companyId: string; accessionNo: string; form: string | null; filed: string | null; url: string | null }) {
  const reader = ACCESSION.test(accessionNo)
    ? href({ view: "filing", companyId, accessionNo, section: statementSection(form ?? "") })
    : null;
  return (
    <span className="comp-filing">
      {reader ? <a href={reader} title={`Open the ${form ?? "filing"} in Tributary's reader (${accessionNo})`}>{form ?? "Filing"}{filed ? ` ${day(filed, true)}` : ""}</a>
        : <span>{form ?? "Filing"}{filed ? ` ${day(filed, true)}` : ""}</span>}
      {url?.startsWith("https://www.sec.gov/") && (
        <a className="edgar-link" href={url} target="_blank" rel="noopener noreferrer" title={`The filing on EDGAR, sec.gov (${accessionNo})`}> EDGAR ↗</a>
      )}
    </span>
  );
}

function FactRows({ facts, companyId, unit, all = facts }: { facts: ComponentFact[]; companyId: string; unit: string; all?: ComponentFact[] }) {
  return (
    <>
      {facts.map((f) => (
        <tr key={`${f.tag}|${f.period_start}|${f.accession_no}`} className={f.used ? "comp-fact used" : "comp-fact"}>
          <td className="first">{f.role ? (f.role === "+" ? "year to date" : "less: earlier year to date")
            : f.used ? (all.filter((x) => x.used).length > 1 ? "part, added" : "tag used") : "also tagged"}</td>
          <td>{f.role ? roleMark(f.role) : ""}</td>
          <td className="n">{figureText(f.value, unit)}</td>
          <td title={factSource(f)}>{f.taxonomy}:{f.tag}<span className="co-sub-line">{factWhy(f, all)}{f.priority ? ` · rank ${f.priority}` : ""}</span></td>
          <td><Filing companyId={companyId} accessionNo={f.accession_no} form={f.form} filed={f.filed} url={f.filing_url} /></td>
        </tr>
      ))}
    </>
  );
}

function PartRows({ parts, companyId, unit }: { parts: ComponentPart[]; companyId: string; unit: string }) {
  return (
    <>
      {parts.map((p, i) => (
        <tr key={`${p.metric ?? "q"}|${p.period_end}|${i}`} className="comp-fact">
          <td className="first">{p.metric ? partName(p.metric) : `Quarter to ${day(p.period_end, true)}`}</td>
          <td>{roleMark(p.role)}</td>
          <td className="n">{figureText(p.value, unit)}</td>
          <td>{p.citation ? tagText(p.citation.xbrl_tag) : ""}</td>
          <td>{p.citation ? <Filing companyId={companyId} accessionNo={p.citation.accession_no} form={p.citation.form} filed={p.citation.filed} url={p.citation.filing_url} />
            : p.accession_no ? <Filing companyId={companyId} accessionNo={p.accession_no} form={null} filed={null} url={null} /> : null}</td>
        </tr>
      ))}
    </>
  );
}

function FigureCells({ figure, companyId }: { figure: ComponentFigure; companyId: string }) {
  return (
    <>
      <td>{tagText(figure.citation.xbrl_tag)}</td>
      <td><Filing companyId={companyId} accessionNo={figure.citation.accession_no} form={figure.citation.form} filed={figure.citation.filed} url={figure.citation.filing_url} /></td>
    </>
  );
}

/** What one standardised figure is made of (F5): the lines it is computed from, each with the tag the
 * filing used, its value and filing, and the other tags reported for that date (the FactSet/Koyfin
 * "open the line" pattern). The period is chosen from the table's columns. */
export function ComponentsPanel({ token, companyId, metric, label, view, columns, periodEnd, onPeriod, onClose }: {
  token: string;
  companyId: string;
  metric: string;
  label: string;
  view: "quarter" | "ttm";
  columns: { periodEnd: string; label: string }[];
  periodEnd: string;
  onPeriod: (periodEnd: string) => void;
  onClose: () => void;
}) {
  const load = useLoad(`${companyId}|${metric}|${view}|${periodEnd}`, () => fetchComponents(token, companyId, metric, periodEnd, view));
  const id = `comp-${metric}`;
  return (
    <section className="components-panel" aria-labelledby={id}>
      <div className="row wrap comp-h">
        <h3 id={id}>What {/^[A-Z][a-z]/.test(label) ? label[0]!.toLowerCase() + label.slice(1) : label} is made of</h3>
        <label className="small">
          Period{" "}
          <select value={periodEnd} onChange={(e) => onPeriod(e.target.value)}>
            {columns.map((c) => <option key={c.periodEnd} value={c.periodEnd}>{c.label}</option>)}
          </select>
        </label>
        <span className="grow" />
        <button className="btn sm ghost" onClick={onClose}>Close</button>
      </div>
      {!load || load.state === "loading" ? <Skeleton lines={3} label="Loading what the figure is made of" />
        : load.state === "denied" ? <p className="empty locked">Not included in your access.</p>
        : load.state === "error" ? <ErrorPanel what="what the figure is made of" detail={load.message} onRetry={load.retry} />
        : load.data === null ? <p className="empty">Not available for this period, or not here yet: the figures are being updated. Try again in a minute.</p>
        : <Opened c={load.data} label={label} companyId={companyId} />}
    </section>
  );
}

function Opened({ c, label, companyId }: { c: Components; label: string; companyId: string }) {
  const unit = c.figure.unit;
  return (
    <>
      <p className="comp-head"><b>{headline(c, label)}</b></p>
      <p className="small muted">
        {HOW[c.how]}{c.definition ? ` Definition: ${c.definition}.` : ""}
      </p>
      <TableScroll label={`${label}: what it is made of`}>
        <table className="t comp-t">
          <thead>
            <tr>
              <th className="first">Line</th>
              <th aria-label="How it counts" />
              <th className="n">Value</th>
              <th>Tag, as filed</th>
              <th>Filing</th>
            </tr>
          </thead>
          <tbody>
            {c.how === "derived" ? c.components.map((p) => (
              <ComponentRows key={p.metric} p={p} companyId={companyId} />
            )) : (
              <>
                <tr>
                  <td className="first"><b>{label}</b></td>
                  <td />
                  <td className="n"><b>{figureText(c.figure.value, unit)}</b></td>
                  <FigureCells figure={c.figure} companyId={companyId} />
                </tr>
                <PartRows parts={c.parts} companyId={companyId} unit={unit} />
                <FactRows facts={c.facts} companyId={companyId} unit={unit} />
              </>
            )}
          </tbody>
        </table>
      </TableScroll>
      <p className="foot small">
        Standardised by Tributary's rules (the migrations named in the README), from the filings' XBRL. Each line names the tag
        the filing used and links its filing; "also tagged" lines are other tags with a value for the same date, which gold
        ranks after the one used. Nothing here is estimated.
      </p>
    </>
  );
}

function ComponentRows({ p, companyId }: { p: Components["components"][number]; companyId: string }) {
  const unit = p.figure?.unit ?? "USD";
  if (!p.used) {
    return (
      <tr className="comp-left-out">
        <td className="first">{partName(p.metric)}</td>
        <td>{roleMark(p.role)}</td>
        <td className="n faint">—</td>
        <td colSpan={2} className="small muted">{p.note ? LEFT_OUT[p.note] : "Not used."}</td>
      </tr>
    );
  }
  return (
    <>
      <tr className="comp-line">
        <td className="first">{partName(p.metric)}</td>
        <td>{roleMark(p.role)}</td>
        <td className="n">{figureText(p.value, unit)}</td>
        {p.figure ? <FigureCells figure={p.figure} companyId={companyId} /> : <td colSpan={2} className="small muted">as the rule read it</td>}
      </tr>
      <PartRows parts={p.parts ?? []} companyId={companyId} unit={unit} />
      {/* the tag used is the line above; a sum's parts and the tags not used are listed */}
      <FactRows facts={(p.facts ?? []).filter((f) => !f.used || (p.facts ?? []).filter((x) => x.used).length > 1)} all={p.facts ?? []} companyId={companyId} unit={unit} />
    </>
  );
}

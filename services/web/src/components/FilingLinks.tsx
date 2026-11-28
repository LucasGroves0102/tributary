import { statementSection } from "../company/data";
import { day } from "../format";
import { sectionName } from "../reader/sections";
import { href, SECTION } from "../route";

const CIK = /^\d{10}$/;
const ACCESSION = /^\d{10}-\d{2}-\d{6}$/;

/** Where a citation can be read, as two links that say which is which: the section in Tributary's
 * reader ("Read MD&A"), the filing itself on EDGAR ("EDGAR ↗", which leaves the app). The
 * accession number is on the EDGAR link's hover. Reader links for a section a tool returned and ids
 * of the right shape; a 10-K or 10-Q cited for its figures (no section returned) opens at its
 * financial statements in the reader, not only on EDGAR (Marek, finding 24). The EDGAR URL comes
 * from the citation. */
export function FilingLinks({ form, filed, accessionNo, url, companyId, sections }: {
  form: string | null;
  filed: string | null;
  accessionNo: string;
  url: string | null;
  companyId: string | null | undefined;
  sections: string[];
}) {
  const ids = !!companyId && CIK.test(companyId) && ACCESSION.test(accessionNo);
  const readable = ids ? sections.filter((s) => SECTION.test(s)).slice(0, 6) : [];
  const statements = ids && !readable.length && form ? statementSection(form) : null;
  return (
    <>
      <span className="form">{form ?? "Filing"}</span>
      {filed && <span className="muted">{day(filed, true)}</span>}
      {readable.map((s) => (
        <a key={s} className="reader-link" href={href({ view: "filing", companyId: companyId!, accessionNo, section: s })}
          title={`Open ${sectionName(s)} (${s}) in Tributary's filing reader`}>
          Read {sectionName(s)}
        </a>
      ))}
      {statements && (
        <a className="reader-link" href={href({ view: "filing", companyId: companyId!, accessionNo, section: statements })}
          title={`Open the ${form}'s financial statements in Tributary's filing reader`}>
          Read the statements
        </a>
      )}
      {url ? (
        <a className="edgar-link" href={url} target="_blank" rel="noopener noreferrer" title={`The whole filing on EDGAR, sec.gov (${accessionNo})`}>
          EDGAR ↗
        </a>
      ) : (
        <span className="accession">{accessionNo}</span>
      )}
    </>
  );
}

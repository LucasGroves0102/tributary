import { day } from "../format";
import { sectionName } from "../reader/sections";
import { href, SECTION } from "../route";

const CIK = /^\d{10}$/;
const ACCESSION = /^\d{10}-\d{2}-\d{6}$/;

/** Where a citation can be read, as two links that say which is which: the section in Tributary's
 * reader ("Read MD&A"), the filing itself on EDGAR ("EDGAR ↗", which leaves the app). The
 * accession number is on the EDGAR link's hover. Reader links only for a section a tool returned
 * and ids of the right shape; the EDGAR URL comes from the citation. */
export function FilingLinks({ form, filed, accessionNo, url, companyId, sections }: {
  form: string | null;
  filed: string | null;
  accessionNo: string;
  url: string | null;
  companyId: string | null | undefined;
  sections: string[];
}) {
  const readable = companyId && CIK.test(companyId) && ACCESSION.test(accessionNo)
    ? sections.filter((s) => SECTION.test(s)).slice(0, 6)
    : [];
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

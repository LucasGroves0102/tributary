import { useEffect, useState } from "react";

import { ApiError, filings as fetchFilings, section as fetchSection } from "../../api";
import { href } from "../../route";
import type { Filing, Section } from "../../types";

interface Props {
  token: string;
  companyId: string;
  accessionNo: string;
  sectionKey: string | null;
}

/** A parsed filing: its sections on the left, the selected section's text on the right. */
export function FilingReader({ token, companyId, accessionNo, sectionKey }: Props) {
  const [filing, setFiling] = useState<Filing | null | "denied" | "missing">(null);
  const [text, setText] = useState<Section | null | "error">(null);

  useEffect(() => {
    let live = true;
    setFiling(null);
    fetchFilings(token, companyId)
      .then((fs) => live && setFiling(fs.find((f) => f.accession_no === accessionNo) ?? "missing"))
      .catch((e) => live && setFiling(e instanceof ApiError && e.status === 403 ? "denied" : "missing"));
    return () => {
      live = false;
    };
  }, [token, companyId, accessionNo]);

  const active =
    typeof filing === "object" && filing
      ? (filing.sections.find((s) => s.section_key === sectionKey) ?? filing.sections[0] ?? null)
      : null;

  const activeKey = active?.section_key;
  useEffect(() => {
    if (!activeKey) return;
    let live = true;
    setText(null);
    fetchSection(token, companyId, activeKey, accessionNo)
      .then((s) => live && setText(s))
      .catch(() => live && setText("error"));
    return () => {
      live = false;
    };
  }, [token, companyId, accessionNo, activeKey]);

  const back = (
    <a className="back" href={href({ view: "company", companyId })}>
      ← Company
    </a>
  );
  if (filing === null) return <div className="page">{back}<p className="muted">Loading…</p></div>;
  if (filing === "denied") {
    return (
      <div className="page">
        {back}
        <p className="locked">Filing text needs the pro plan and a grant for this company.</p>
      </div>
    );
  }
  if (filing === "missing") {
    return <div className="page">{back}<p className="error">Filing {accessionNo} not found.</p></div>;
  }

  return (
    <div className="page reader">
      {back}
      <header className="page-header">
        <div>
          <h1>
            <span className="form">{filing.form}</span> filed {filing.filed}
          </h1>
          <p className="muted">
            {accessionNo}
            {filing.period_of_report && ` · period ${filing.period_of_report}`}
          </p>
        </div>
        {filing.document_url?.startsWith("https://www.sec.gov/") && (
          <a className="external" href={filing.document_url} target="_blank" rel="noopener noreferrer">
            Original on EDGAR ↗
          </a>
        )}
      </header>
      <div className="reader-body">
        <nav className="toc" aria-label="Sections">
          {filing.sections.map((s) => (
            <a
              key={s.section_key}
              href={href({ view: "filing", companyId, accessionNo, section: s.section_key })}
              aria-current={s.section_key === active?.section_key ? "page" : undefined}
            >
              <span className="key">{s.section_key}</span>
              <span>{s.title ?? "Untitled"}</span>
            </a>
          ))}
        </nav>
        <article className="section-text">
          {text === null && <p className="muted">Loading…</p>}
          {text === "error" && <p className="error">Couldn't load this section.</p>}
          {text && text !== "error" && (
            <>
              <h2>
                {text.section_key} · {text.title ?? "Untitled"}
              </h2>
              {text.text
                .split(/\n{2,}|\n(?=[A-Z•])/)
                .filter((p) => p.trim())
                .map((p, i) => (
                  <p key={i}>{p.trim()}</p>
                ))}
              {text.truncated && (
                <p className="muted small">
                  Showing the first {text.text.length.toLocaleString()} of{" "}
                  {text.total_chars.toLocaleString()} characters; the rest is in the original
                  filing.
                </p>
              )}
            </>
          )}
        </article>
      </div>
    </div>
  );
}

import { Fragment, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from "react";

import {
  filingIndex,
  parsedFiling,
  searchFilings,
  section as fetchSection,
  sectionChanges,
} from "../../api";
import { Changes, edgarLink } from "../../company/Filings";
import { Choices } from "../Choices";
import { addDays } from "../../home/data";
import { FormMark } from "../FormMark";
import { Mark } from "../Mark";
import { useLoad, type Load } from "../../company/useLoad";
import { ErrorPanel, Skeleton } from "../States";
import { day, periodPhrase } from "../../format";
import {
  comparableForm,
  highlight,
  nextComparable,
  outlineGroups,
  priorComparable,
  searchTerms,
  snippetRuns,
  steppedForm,
  type Run,
} from "../../reader/text";
import { blockAt, blockMatches, firstBlockMatch, framedFigure, readerBlocks, type Block } from "../../reader/blocks";
import { sectionTitle, statementsElsewhere } from "../../reader/sections";
import { go, href } from "../../route";
import { asPhrase, isPhrase } from "../../search/state";
import { TableScroll } from "../TableScroll";

/** A loaded section's blocks, read once per answer (the match count is worked out on every render). */
const BLOCKS = new WeakMap<object, Block[]>();
function blocksOf(section: { text: string }): Block[] {
  let b = BLOCKS.get(section);
  if (!b) BLOCKS.set(section, (b = readerBlocks(section.text)));
  return b;
}
import type { IndexedFiling, SectionChanges, SectionHit } from "../../types";
import { type Basis, comparisonLabel, defaultBasis, fallBack, periodLabel } from "../../company/changes";

interface Props {
  token: string;
  companyId: string;
  accessionNo: string;
  sectionKey: string | null;
  /** what's open in words, once it has loaded: "10-K filed Feb 21, 2026" */
  onFiling?: (label: string) => void;
  /** a document character offset to scroll to (a citation's char_start) */
  at?: number;
  /** with `at`: the end of a printed figure a card opened (168): framed, and ticked when it agrees */
  to?: number;
  figure?: "agrees" | "disagrees";
  /** words to highlight, and to search the filing for */
  q?: string;
  /** the company's name, when known */
  name?: string | null;
  onAsk?: (question: string) => void;
  /** called once the filing's text is readable, and again for each section opened (Home's
   * "Continue where you left off") */
  onRead?: (sectionKey: string | null) => void;
  /** What changed (a 10-K or 10-Q): the filing's changes in place of a section's text */
  mode?: "changes";
}

const SEARCH_FORMS = /^(10-K|10-Q|8-K|20-F|40-F|6-K)(\/A)?$/;
/** The filing index's largest page. */
const INDEX_MAX = 100;
const NEXT_8K_WITHIN_DAYS = 400;

/** A parsed filing: its outline on the left (grouped by Part, with search matches above it), the
 * selected section on the right, highlighted and scrolled to a cited passage or the first match,
 * with next and previous match. It steps to the previous and next filing of the same form, at
 * the same section with the same search. A 10-K or 10-Q also shows What changed side by side.
 *
 * Two counts on purpose, each labelled: the outline shows *passages* (the search index's units of
 * about 2,000 characters that hold all the words, from the API), the section heading shows the
 * same passages and *highlights* (each word marked in the text, counted here, what next and
 * previous step through). */
export function FilingReader({ token, companyId, accessionNo, sectionKey, at, to, figure, q, name, onAsk, onRead, onFiling, mode }: Props) {
  const filing = useLoad(`${companyId}|${accessionNo}`, () => parsedFiling(token, companyId, accessionNo));
  const f = filing?.state === "ok" ? filing.data : null;
  const active = f ? (f.sections.find((s) => s.section_key === sectionKey) ?? f.sections[0] ?? null) : null;
  const readRef = useRef(onRead);
  readRef.current = onRead;
  // the breadcrumb and tab title say what's open in words
  const openLabel = f ? `${f.form} filed ${day(f.filed, true)}` : null;
  useEffect(() => {
    if (openLabel) onFiling?.(openLabel);
  }, [openLabel]);
  const readKey = f ? `${accessionNo}|${active?.section_key ?? ""}` : null;
  useEffect(() => {
    if (readKey) readRef.current?.(readKey.split("|")[1] || null);
  }, [readKey]);
  const text = useLoad(active ? `${accessionNo}|${active.section_key}` : null, () =>
    fetchSection(token, companyId, active!.section_key, accessionNo),
  );
  const form = f ? comparableForm(f.form) : null;
  // F3: read for materiality (calendar rollovers and split paragraphs hidden), a 10-Q against the
  // same quarter a year earlier unless that isn't indexed (then the previous one, once)
  const [readerBasis, setReaderBasis] = useState<Basis | null>(null);
  const basis = readerBasis ?? defaultBasis(f?.form);
  const changes = useLoad(form ? `${accessionNo}|${basis}` : null, () =>
    sectionChanges(token, companyId, accessionNo, { view: "reading", basis }));
  useEffect(() => {
    if (changes?.state === "ok" && fallBack(changes.data, basis, readerBasis !== null)) setReaderBasis("prior");
  }, [changes]);
  useEffect(() => setReaderBasis(null), [accessionNo]);
  const stepped = f ? steppedForm(f.form) : null;
  const prior = useLoad(stepped && f ? accessionNo : null, () =>
    filingIndex(token, companyId, { forms: [stepped!], to: f!.filed, limit: 5 }).then((r) => priorComparable(r.filings, f!)),
  );
  const later = useLoad(stepped && f ? accessionNo : null, () =>
    // newest first, so a year's window for the form that can have more than a page of later ones
    filingIndex(token, companyId, {
      forms: [stepped!], from: f!.filed, limit: INDEX_MAX,
      ...(stepped === "8-K" ? { to: addDays(f!.filed, NEXT_8K_WITHIN_DAYS) } : {}),
    }).then((r) => nextComparable(r.filings, f!)),
  );

  const [draft, setDraft] = useState(q ?? "");
  const [query, setQuery] = useState(q ?? "");
  useEffect(() => {
    setDraft(q ?? "");
    setQuery(q ?? "");
  }, [q, accessionNo]);
  const hits = useLoad(query && f ? `${accessionNo}|${query}` : null, () =>
    searchFilings(token, {
      q: query,
      company: companyId,
      form: SEARCH_FORMS.test(f!.form) ? f!.form : undefined,
      from: f!.filed,
      to: f!.filed,
      limit: 50,
    }).then((r) => r.results.filter((h) => h.citation.accession_no === accessionNo)),
  );
  const terms = useMemo(() => searchTerms(query), [query]);
  // which highlight is current; -1: SectionBody sets it to where the section opens
  const [match, setMatch] = useState(-1);
  useEffect(() => setMatch(-1), [accessionNo, active?.section_key, query, at]);
  // the section whose What changed is open (closes when another section opens)
  const [diffFor, setDiffFor] = useState<string | null>(null);
  // on a narrow page the outline folds behind "Contents" (spec §3), open on a tap
  const [tocOpen, setTocOpen] = useState(false);

  const back = (
    <p className="crumb">
      <a href={href({ view: "company", companyId })}>{name ?? "Company"}</a>
      <span aria-hidden="true"> / </span>
      <a className="back" href={href({ view: "company", companyId, tab: "filings" })}>Filings</a>
    </p>
  );
  if (!filing || filing.state === "loading") return <div className="page">{back}<Skeleton lines={8} label="Loading the filing" /></div>;
  if (filing.state === "denied") {
    return (
      <div className="page">
        {back}
        <p className="empty locked">
          Filing text needs filings access (the pro plan and a grant for this company). The filing
          index on the company's Filings tab links every filing to EDGAR.
        </p>
      </div>
    );
  }
  if (filing.state === "error" || !f) {
    return (
      <div className="page">
        {back}
        {filing.state === "error" ? (
          <ErrorPanel what="this filing" detail={filing.message} onRetry={filing.retry} />
        ) : (
          <p className="empty">
            Filing {accessionNo} hasn't been parsed into sections yet. The company's Filings tab
            links it on EDGAR in the meantime.
          </p>
        )}
      </div>
    );
  }

  const diff = changes?.state === "ok" ? changes.data.sections.find((s) => s.section_key === active?.section_key) : undefined;
  const diffOn = !!active && diffFor === active.section_key;
  const hitsBySection = new Map<string, SectionHit>(hits?.state === "ok" ? hits.data.map((h) => [h.section_key, h]) : []);
  const sectionText = text?.state === "ok" ? text.data : null;
  const matches = terms.length && sectionText ? blocksOf(sectionText).reduce((n, b) => n + blockMatches(b, terms), 0) : 0;
  const step = (by: 1 | -1) => matches > 0 && setMatch((m) => ((m < 0 ? (by > 0 ? -1 : 0) : m) + by + matches) % matches);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const words = draft.trim();
    // Enter on the words already searched: the next match, as in a browser's find
    if (words && words === query && matches > 0) {
      step(1);
      return;
    }
    go({ view: "filing", companyId, accessionNo, section: active?.section_key ?? null, ...(words ? { q: words } : {}) });
    setQuery(words);
  };
  const onSearchKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && e.shiftKey && draft.trim() === query && matches > 0) {
      e.preventDefault();
      step(-1);
    }
  };
  const index = f.sections.findIndex((s) => s.section_key === active?.section_key);
  const neighbour = (i: number) => f.sections[i] && (
    <a href={href({ view: "filing", companyId, accessionNo, section: f.sections[i]!.section_key, ...(query ? { q: query } : {}) })}>
      {i < index ? "← " : ""}{f.sections[i]!.section_key} · {sectionTitle(f.sections[i]!.section_key, f.sections[i]!.title)}{i > index ? " →" : ""}
    </a>
  );

  // What changed (a 10-K or 10-Q): the filing's changes in the page's place, the outline counting them
  const changesOn = mode === "changes" && !!form;
  const changed = new Map<string, number>(changes?.state === "ok" ? changes.data.sections.map((s) => [s.section_key, s.material
    ? s.material.new + s.material.removed + s.material.rewritten
    : s.summary.new + s.summary.removed + s.summary.rewritten]) : []);
  const changedTotal = [...changed.values()].reduce((a, b) => a + b, 0);
  const toMode = (m: "read" | "changes") =>
    go({ view: "filing", companyId, accessionNo, section: active?.section_key ?? null, ...(query ? { q: query } : {}), ...(m === "changes" ? { mode: "changes" as const } : {}) });

  return (
    <div className={changesOn ? "page reader changes-mode" : "page reader"}>
      <header className="reader-head">
        {back}
        <div className="page-bar">
          <h1>
            <FormMark form={f.form} /> {name ? `${name}, ` : ""}filed {day(f.filed, true)}
          </h1>
          <div className="page-actions">
            {f.document_url?.startsWith("https://www.sec.gov/") && (
              <a className="btn external" href={f.document_url} target="_blank" rel="noopener noreferrer">
                Original on EDGAR ↗
              </a>
            )}
            {onAsk && active && (
              <button className="btn primary" onClick={() => onAsk(`Summarize ${active.title ?? `section ${active.section_key}`} in this ${f.form}.`)}>
                Ask about this section
              </button>
            )}
          </div>
        </div>
        <p className="reader-meta">
          <span className="code">{accessionNo}</span>
          {f.period_of_report && <span>{periodPhrase(f.form, f.period_of_report)}</span>}
          <span>{f.sections.length} {f.sections.length === 1 ? "section" : "sections"}</span>
        </p>
      </header>
      <div className="reader-tools">
        {form && (
          <Choices label="Read the filing or what changed" value={changesOn ? "changes" : "read"} set={toMode}
            options={[["read", "Read"], ["changes", changedTotal ? `What changed ${changedTotal.toLocaleString()}` : "What changed"]]} />
        )}
        {!changesOn && (
        <form className="reader-search" role="search" onSubmit={submit}>
          <input className="field" type="search" aria-label="Search this filing" placeholder="Search this filing: words or “a phrase”"
            value={draft} maxLength={200} onChange={(e) => setDraft(e.target.value)} onKeyDown={onSearchKey} />
          <button className="btn sm" type="submit">Search</button>
          <label className="chk small" title="Match the words together, in this order (the same as typing them in quotes)">
            <input type="checkbox" checked={isPhrase(draft)} disabled={!draft.trim()}
              onChange={(e) => {
                // #80: applies at once, like the search page's filters
                const next = asPhrase(draft, e.target.checked);
                setDraft(next);
                go({ view: "filing", companyId, accessionNo, section: active?.section_key ?? null, ...(next ? { q: next } : {}) });
                setQuery(next);
              }} />
            <span>Exact phrase</span>
          </label>
        </form>
        )}
        {stepped && active && (
          <span className="filing-steps" role="group" aria-label={`Other ${stepped} filings`}>
            <FilingStep load={prior} dir="prior" form={stepped} companyId={companyId} sectionKey={active.section_key} q={query} />
            <FilingStep load={later} dir="next" form={stepped} companyId={companyId} sectionKey={active.section_key} q={query} />
          </span>
        )}
      </div>
      <div className="reader-body">
        <button type="button" className="btn toc-toggle" aria-expanded={tocOpen} aria-controls="reader-toc" onClick={() => setTocOpen((o) => !o)}>
          Contents<span className="more-caret" aria-hidden="true" />
        </button>
        <nav id="reader-toc" className={tocOpen ? "toc" : "toc folded"} aria-label="Sections">
          {query && <SearchHits hits={hits} query={query} companyId={companyId} accessionNo={accessionNo} />}
          {query && <p className="toc-legend faint small">Number: passages that match, about 2,000 characters each</p>}
          {outlineGroups(f.sections).map((g) => (
            <div key={g.label} className="grp">
              <div className="grp-h">{g.label}</div>
              {g.sections.map((s) => {
                const hit = changesOn ? undefined : hitsBySection.get(s.section_key);
                const n = changesOn ? changed.get(s.section_key) : undefined;
                return (
                  <a
                    key={s.section_key}
                    href={href({ view: "filing", companyId, accessionNo, section: s.section_key, ...(query ? { q: query } : {}) })}
                    aria-current={!changesOn && s.section_key === active?.section_key ? "page" : undefined}
                    title={changesOn ? `Read ${sectionTitle(s.section_key, s.title)}` : `${s.chars.toLocaleString("en-US")} characters`}
                  >
                    {/* the item's number under its Part's heading, not the stored key ("II-1A"), and no "38k"
                        counts in the outline: the size is the hover (Riley 81) */}
                    <span className="key">{s.section_key.replace(/^(I|II|III|IV)-/, "")}</span>
                    <span className="grow">{sectionTitle(s.section_key, s.title)}</span>
                    {hit && (
                      <span className="cnt" title={`${hit.passages_matched} matching passage${hit.passages_matched === 1 ? "" : "s"}`}>
                        {hit.passages_matched}
                      </span>
                    )}
                    {n !== undefined && n > 0 && (
                      <span className="cnt changed" title={`${n} ${n === 1 ? "change" : "changes"} in this section`}>{n}</span>
                    )}
                  </a>
                );
              })}
            </div>
          ))}
        </nav>
        {changesOn ? (
          <article className="section-text changes-page" aria-label="What changed">
            <Changes token={token} companyId={companyId} accessionNo={accessionNo} name={name ?? "This company"} form={f.form} />
          </article>
        ) : (
        <article className="section-text" data-sticky-scope="">
          {sectionKey && active && active.section_key !== sectionKey && (
            <p className="faint small">This filing has no section {sectionKey}; showing {active.section_key}.</p>
          )}
          {active === null && <p className="empty">This filing has no parsed sections.</p>}
          {active && (
            <div className="row wrap section-h" data-sticky-bar="">
              <h2 className="grow">
                {active.section_key} · {sectionTitle(active.section_key, active.title)}
              </h2>
              {terms.length > 0 && sectionText && !diffOn && (
                <MatchNav matches={matches} current={match} passages={hitsBySection.get(active.section_key)?.passages_matched ?? 0}
                  searched={hits?.state === "ok"} query={query} onStep={step}
                  elsewhere={(() => {
                    // none here but some in another section: say where, with the way there (Riley, test 10, finding 62)
                    const other = hits?.state === "ok" ? hits.data.find((h) => h.section_key !== active.section_key && h.passages_matched > 0) : undefined;
                    const s = other ? f.sections.find((x) => x.section_key === other.section_key) : undefined;
                    return other && s ? {
                      label: sectionTitle(s.section_key, s.title),
                      href: href({ view: "filing", companyId, accessionNo, section: s.section_key, ...(query ? { q: query } : {}) }),
                    } : null;
                  })()} />
              )}
              {diff && (
                <button className="btn sm" aria-pressed={diffOn} onClick={() => setDiffFor(diffOn ? null : active.section_key)}>
                  {diffOn ? "Back to text" : `What changed (${diff.material
                    ? diff.material.new + diff.material.removed + diff.material.rewritten
                    : diff.summary.new + diff.summary.removed + diff.summary.rewritten})`}
                </button>
              )}
            </div>
          )}
          {active && (() => {
            // a 10-K's Item 8 that only points to the statements: link where they are (Riley, test 10, finding 10)
            const at = statementsElsewhere(f.form, f.sections, active.section_key);
            return at && (
              <p className="statements-elsewhere" role="note">
                The financial statements themselves are in Item {at.section_key.replace(/^(I|II|III|IV)-/, "")},{" "}
                {sectionTitle(at.section_key, at.title)}:{" "}
                <a href={href({ view: "filing", companyId, accessionNo, section: at.section_key, ...(query ? { q: query } : {}) })}>read them there</a>.
              </p>
            );
          })()}
          {active && diffOn && diff ? (
            <SideBySide diff={diff} companyId={companyId} basis={changes?.state === "ok" ? changes.data.basis : basis}
              form={f?.form ?? null} onBasis={f?.form.startsWith("10-Q") ? setReaderBasis : undefined} />
          ) : (
            active && <SectionBody load={text} terms={terms} at={at} to={to} figure={figure} current={match} onOpen={setMatch} />
          )}
          {active && (
            <div className="row section-nav">
              <span className="grow">{neighbour(index - 1)}</span>
              <span>{neighbour(index + 1)}</span>
            </div>
          )}
        </article>
        )}
      </div>
    </div>
  );
}

function Marked({ runs }: { runs: Run[] }) {
  return <>{runs.map((r, i) => (typeof r === "string" ? r : <mark key={i}>{r.mark}</mark>))}</>;
}

/** "Highlight 3 of 40" with previous and next, and the section's passage count: the outline's
 * number, so the two can be read together. */
function MatchNav({ matches, current, passages, searched, query, onStep, elsewhere = null }: {
  matches: number;
  current: number;
  passages: number;
  /** the passage count has come back (0 then means none hold all the words) */
  searched: boolean;
  query: string;
  onStep: (by: 1 | -1) => void;
  /** the first other section with matching passages, when this one has none */
  elsewhere?: { label: string; href: string } | null;
}) {
  const of = passages === 1 ? "1 passage" : `${passages} passages`;
  if (searched && passages === 0 && matches === 0) {
    return (
      <span className="match-nav" role="status">
        <span className="faint">No match for “{query}” in this section.</span>
        {elsewhere && <a href={elsewhere.href}>Matches in {elsewhere.label}</a>}
      </span>
    );
  }
  return (
    <span className="match-nav" role="status">
      <span className="faint">
        {searched && `${of} match “${query}”. `}
        {matches === 0
          ? "No words to highlight here."
          : `Highlight ${Math.max(current, 0) + 1} of ${matches}.`}
      </span>
      {matches > 1 && (
        <>
          <button type="button" className="btn sm ghost icon" aria-label="Previous match" title="Previous match (Shift+Enter in the search box)" onClick={() => onStep(-1)}>↑</button>
          <button type="button" className="btn sm ghost icon" aria-label="Next match" title="Next match (Enter in the search box)" onClick={() => onStep(1)}>↓</button>
        </>
      )}
    </span>
  );
}

function SectionBody({ load, terms, at, to, figure, current, onOpen }: {
  load: Load<Awaited<ReturnType<typeof fetchSection>>> | null;
  terms: string[];
  at?: number;
  to?: number;
  figure?: "agrees" | "disagrees";
  /** the current highlight; -1: not chosen yet, onOpen sets where the section opens */
  current: number;
  onOpen: (highlight: number) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const section = load?.state === "ok" ? load.data : null;
  // #178/#179: paragraphs and tables, page debris left out (reader/blocks.ts)
  const blocks = useMemo(() => (section ? blocksOf(section) : []), [section]);
  const start = section?.citation.char_start;
  const target = at !== undefined && start !== undefined ? blockAt(blocks, at - start) : -1;
  const beyond = section && at !== undefined && start !== undefined && at - start >= section.text.length;
  // 168: the printed figure a card opened, framed where its block shows it (reader/blocks.ts rule 8)
  const framed = useMemo(
    () => (section && at !== undefined && to !== undefined && start !== undefined
      ? framedFigure(section.text, blocks, at - start, to - start) : null),
    [section, blocks, at, to, start]);
  const hasTerms = terms.length > 0;
  // opening a section with a search: the first highlight, or the first at the cited passage
  useEffect(() => {
    if (section && hasTerms && current < 0) onOpen(firstBlockMatch(blocks, terms, target));
  }, [section, hasTerms, current, blocks, terms, target, onOpen]);
  useEffect(() => {
    if (!section) return;
    const el = (framed && current < 0 ? ref.current?.querySelector(".figure-frame") : null)
      ?? ref.current?.querySelector(current >= 0 ? "mark.cur" : ".hit") ?? ref.current?.querySelector(".hit");
    el?.scrollIntoView?.({ block: "center" });
  }, [section, target, current, framed]);
  let marks = 0;
  // highlights numbered in blockUnits' order, so next/previous and the count agree
  const marked = (s: string) =>
    highlight(s, terms).map((r, j) => {
      if (typeof r === "string") return r;
      const n = marks++;
      return <mark key={j} className={n === current ? "cur" : undefined}>{r.mark}</mark>;
    });
  // each block's units in blockUnits' order: the framed figure's unit splits around it
  let unit = 0;
  const piece = (s: string, block: number) => {
    const u = unit++;
    if (!framed || framed.block !== block || framed.unit !== u) return marked(s);
    const end = framed.offset + framed.length;
    return [
      <Fragment key="before">{marked(s.slice(0, framed.offset))}</Fragment>,
      <span key="figure" className={figure === "disagrees" ? "figure-frame differs" : figure === "agrees" ? "figure-frame agrees" : "figure-frame"}>
        {s.slice(framed.offset, end)}
        {figure === "agrees" && <Mark kind="ok" />}
      </span>,
      <Fragment key="after">{marked(s.slice(end))}</Fragment>,
    ];
  };

  if (!load || load.state === "loading") return <Skeleton lines={6} label="Loading the section" />;
  if (load.state === "denied") return <p className="empty locked">This section isn't included in your access.</p>;
  if (load.state === "error" || !section) {
    return <ErrorPanel what="this section" onRetry={load.state === "error" ? load.retry : undefined} />;
  }
  return (
    <div ref={ref}>
      {framed && figure && (
        <p className={`figure-note small${figure === "disagrees" ? " differs" : ""}`}>
          {figure === "agrees"
            ? "Framed: the figure you opened, where this filing prints it. Its inline XBRL ties the two, and they agree."
            : "Framed: where this filing prints the figure you opened. The number printed here differs from it: the data and the page disagree."}
        </p>
      )}
      {blocks.map((b, i) => {
        unit = 0;
        if (b.kind === "p") return <p key={b.start} className={i === target ? "hit" : undefined}>{piece(b.text, i)}</p>;
        // a table's first rows with no figures ("(In millions, except per share data)", "(Unaudited)", "Three Months
        // Ended") are its heading, not data: they read in the head, under the dates, in the order they're counted
        // (Riley, test 10, finding 84; the parser keeps them as rows)
        const lead = b.rows.findIndex((r) => r.cells.length > 0);
        const notes = lead > 0 ? b.rows.slice(0, lead) : [];
        const body = notes.length ? b.rows.slice(lead) : b.rows;
        return (
          <div key={b.start} className={i === target ? "hit filing-table" : "filing-table"}>
            <TableScroll label="A table in the filing">
              <table className="t ftable">
                {(b.heads || notes.length > 0) && (
                  <thead>
                    {b.heads && (
                      <tr>
                        <th className="first">{piece(b.heads.label, i)}</th>
                        {b.heads.cells.map((c, k) => <th key={k} className="n">{piece(c, i)}</th>)}
                      </tr>
                    )}
                    {notes.map((r) => (
                      <tr key={r.start} className="head-note">
                        <th colSpan={b.columns + 1} scope="colgroup">{piece(r.label, i)}</th>
                      </tr>
                    ))}
                  </thead>
                )}
                <tbody>
                  {body.map((r) =>
                    r.cells.length ? (
                      <tr key={r.start}>
                        <td className="first">{piece(r.label, i)}</td>
                        {r.cells.map((c, k) => <td key={k} className="n">{piece(c, i)}</td>)}
                      </tr>
                    ) : (
                      <tr key={r.start} className="label-row">
                        <td colSpan={b.columns + 1}>{piece(r.label, i)}</td>
                      </tr>
                    ),
                  )}
                </tbody>
              </table>
            </TableScroll>
          </div>
        );
      })}
      {section.truncated && (
        <p className="muted small">
          Showing the first {section.text.length.toLocaleString()} of {section.total_chars.toLocaleString()} characters
          {beyond ? "; the cited passage is further on, in the original filing" : "; the rest is in the original filing"}.
        </p>
      )}
    </div>
  );
}

function SearchHits({ hits, query, companyId, accessionNo }: { hits: Load<SectionHit[]> | null; query: string; companyId: string; accessionNo: string }) {
  let body: ReactNode;
  if (!hits || hits.state === "loading") body = <p className="faint small">Searching…</p>;
  else if (hits.state === "denied") body = <p className="faint small">Searching needs filings access.</p>;
  else if (hits.state === "error") body = <p className="faint small error">{hits.message}</p>;
  else if (!hits.data.length) body = <p className="faint small">No passage in this filing matches. Search covers the indexed sections.</p>;
  else {
    body = (
      <ul className="hits">
        {hits.data.map((h) => (
          <li key={h.section_key}>
            <a href={href({ view: "filing", companyId, accessionNo, section: h.section_key, at: h.citation.char_start, q: query })}>
              <b>{h.section_key}</b> {h.title ?? ""}
            </a>
            <div className="snippet"><Marked runs={snippetRuns(h.snippet, searchTerms(query))} /></div>
          </li>
        ))}
      </ul>
    );
  }
  return (
    <div className="grp search-hits">
      <div className="grp-h">Matches for “{query}”</div>
      {body}
    </div>
  );
}

/** The filing before or after this one of the same form, opened at the same section with the same
 * search; one that isn't parsed links to EDGAR. */
function FilingStep({ load, dir, form, companyId, sectionKey, q }: { load: Load<IndexedFiling | null> | null; dir: "prior" | "next"; form: string; companyId: string; sectionKey: string; q: string }) {
  if (load?.state !== "ok") return null;
  const p = load.data;
  const word = dir === "prior" ? "Prior" : "Next";
  if (!p) {
    // a later 8-K is only looked for within a year (see above), so no "latest" claim for it
    return dir === "next" && form === "8-K" ? null : (
      <span className="faint small">{dir === "prior" ? `No earlier ${form} on file` : `Latest ${form} on file`}</span>
    );
  }
  const label = `${word} ${form}, filed ${day(p.filed, true)}`;
  if (p.sections_available) {
    return (
      <a className="btn sm ghost" title="The same section, with the same search"
        href={href({ view: "filing", companyId, accessionNo: p.accession_no, section: sectionKey, ...(q ? { q } : {}) })}>
        {dir === "prior" ? `← ${label}` : `${label} →`}
      </a>
    );
  }
  const edgar = edgarLink(p);
  return edgar ? (
    <a className="btn sm ghost" href={edgar} target="_blank" rel="noopener noreferrer" title="Not parsed in Tributary">
      {word} {form} on EDGAR ↗
    </a>
  ) : null;
}

type DiffSection = SectionChanges["sections"][number];

/** What changed in this section since the comparable filing: prior on the left, this filing on
 * the right; rewritten paragraphs mark removed words on the left and added words on the right. A
 * 10-Q can switch between the same quarter a year earlier and the previous 10-Q (`onBasis`); a
 * passage that names its period says so (F3). */
function SideBySide({ diff, companyId, basis, form, onBasis }: {
  diff: DiffSection;
  companyId: string;
  basis: Basis;
  form: string | null;
  onBasis?: (b: Basis) => void;
}) {
  const c = diff.compared_to;
  const m = diff.material;
  const hidden = m ? m.hidden.calendar + m.hidden.resplit + m.hidden.figures_only + m.hidden.minor + (m.hidden.comparative ?? 0) : 0;
  return (
    <div className="sbs-wrap">
      <div className="row wrap">
        {onBasis && (
          <span className="seg" role="group" aria-label="Compare with">
            {(["year_ago", "prior"] as const).map((b) => (
              <button key={b} className={basis === b ? "on" : undefined} aria-pressed={basis === b} onClick={() => onBasis(b)}>
                {comparisonLabel(b, form)}
              </button>
            ))}
          </span>
        )}
        <span className="tag q">{m ? m.new : diff.summary.new} new</span>
        <span className="tag e">{m ? m.rewritten : diff.summary.rewritten} rewritten</span>
        <span className="tag warn">{m ? m.removed : diff.summary.removed} removed</span>
        <span className="faint">
          · {diff.summary.unchanged + hidden} unchanged or minor, hidden
        </span>
      </div>
      {diff.stale ? (
        <p className="faint">This filing was re-parsed since the comparison was made; it will be recomputed.</p>
      ) : diff.changes.length === 0 ? (
        <p className="empty">No substantive paragraph changes in this section.</p>
      ) : (
        <div className="sbs" role="table" aria-label="What changed">
          <div className="sbs-h" role="row">
            <span role="columnheader">
              <a href={href({ view: "filing", companyId, accessionNo: c.accession_no, section: c.section_key })}>
                {basis === "year_ago" ? `${c.form} a year earlier` : `Prior ${c.form}`}, filed {day(c.filed, true)}
              </a>
            </span>
            <span role="columnheader">This filing</span>
          </div>
          {diff.changes.map((ch, i) => (
            <div key={i} className={`sbs-r ${ch.kind}`} role="row">
              <div role="cell" className="old">
                {ch.kind === "removed" && periodLabel(ch.period) && <><span className="tag period">{periodLabel(ch.period)}</span>{" "}</>}
                {ch.kind === "new" ? (
                  <span className="faint">not in the prior filing</span>
                ) : ch.kind === "rewritten" && ch.words ? (
                  // the earlier filing's own words: a calendar swap's "=" run carries them third (Riley 5)
                  ch.words.filter(([op]) => op !== "+").map(([op, t, before], j) => (op === "-" ? <del key={j}>{t} </del> : <span key={j}>{before ?? t} </span>))
                ) : (
                  ch.prior_text
                )}
              </div>
              <div role="cell" className="new">
                {ch.kind !== "removed" && periodLabel(ch.period) && <><span className="tag period">{periodLabel(ch.period)}</span>{" "}</>}
                {ch.kind === "removed" ? (
                  <span className="faint">removed</span>
                ) : ch.kind === "rewritten" && ch.words ? (
                  ch.words.filter(([op]) => op !== "-").map(([op, t], j) => (op === "+" ? <ins key={j}>{t} </ins> : <span key={j}>{t} </span>))
                ) : (
                  ch.text
                )}
              </div>
            </div>
          ))}
        </div>
      )}
      <div className="faint small">Paragraph-level comparison; changes to numbers alone are not shown.</div>
    </div>
  );
}

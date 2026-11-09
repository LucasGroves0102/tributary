import { useEffect, useId, useMemo, useRef, useState, type ChangeEvent, type MutableRefObject } from "react";

import type { Readable } from "../access";
import { draftKey, restoreDraft, type StoredDraft, type ThesisHandle, type ThesisStatus } from "../analysis/bridge";
import { hasOwnText, placePin } from "../analysis/project";
import { ApiError, failureReason, itemVersion, itemVersions, openItem, screenerColumns, workspace as fetchWorkspace } from "../api";
import { slug } from "../chart/export";
import { catalog } from "../chart/metrics";
import type { ChartState } from "../chart/model";
import { leftText, useAllowanceContext } from "../assistant/useAllowance";
import { useDirectory } from "../companies/directory";
import { CompanySearch } from "../components/CompanySearch";
import { ErrorPanel, Skeleton } from "../components/States";
import { download } from "../download";
import { day } from "../format";
import { renderMarkdown } from "../markdown";
import { Embed } from "../reports/Embeds";
import { href } from "../route";
import { load, remove, save as store } from "../storage";
import type { ItemVersion, SavedItemFull } from "../types";
import { ITEM_NAME_MAX, KIND_LABELS, nameProblem } from "../workspaces/model";
import { useEditorKeys } from "../workspaces/useEditor";
import type { Saved } from "../workspaces/useSaved";
import { clearHighlights, findQuote, highlightQuote, textHash } from "./check";
import { BlockEditor, type BlockEditorHandle } from "./BlockEditor";
import { CheckPanel } from "./CheckPanel";
import { checkSection, embedSummary } from "./export";
import {
  EMBEDDABLE,
  embeddedIds,
  importMarkdown,
  insertEmbed,
  MAX_IMPORT_BYTES,
  MAX_MARKDOWN,
  restoreThesis,
  thesisMarkdown,
  thesisSegments,
  thesisState,
  type EmbedItem,
  type Thesis,
} from "./model";
import { useNewerFiling } from "./useRevisit";
import { useThesisCheck, type ThesisChecks } from "./useThesisCheck";

interface Props {
  token: string;
  itemId: string;
  saved: Saved;
  /** company id → ticker or name */
  label: (companyId: string) => string;
  /** companies whose financials the viewer can read (comps embeds) */
  readable: Readable;
  onOpenChart: (s: ChartState) => void;
  /** whose browser drafts these are (unsaved changes are kept per user and thesis) */
  user?: string | null;
  /** inside an analysis project: no "← workspace" link, no company picker (the project's) */
  embedded?: boolean;
  /** the visible editor: only it answers Ctrl+S (a project keeps hidden steps mounted) */
  active?: boolean;
  /** the project's handle on this editor (pins, quotes) */
  bridge?: MutableRefObject<ThesisHandle | null>;
  /** unsaved changes and the saved text, for the project's step markers */
  onStatus?: (s: ThesisStatus) => void;
  /** bumps when the workspace's items changed (a chart pinned): what can be embedded is read again */
  itemsVersion?: number;
  /** the project's checks of this thesis (shared with its Monitor step) */
  checks?: ThesisChecks;
}

type Loaded =
  | { state: "loading" }
  | { state: "ok"; item: SavedItemFull; thesis: Thesis; items: EmbedItem[] }
  | { state: "error"; message: string; notFound: boolean };

/** write: the text with its charts, comps, models and quotes as cards (BlockEditor); markdown: the
 * same text as saved, embed lines and all; preview: rendered; split: write beside preview. */
export type Mode = "write" | "markdown" | "preview" | "split";
const MODE_KEY = "tributary.thesis.mode";
const MODES: [Mode, string, string][] = [
  ["write", "Write", "Your text, with charts, comps, models and quotes as cards you can move and remove"],
  ["markdown", "Markdown", "The text as it is saved and exported, embed lines and all"],
  ["preview", "Preview", "The thesis as it reads"],
  ["split", "Side by side", "Write beside the preview"],
];

function storedMode(): Mode {
  const m = load(MODE_KEY);
  if (m === "write" || m === "markdown" || m === "preview" || m === "split") return m;
  return typeof window !== "undefined" && window.innerWidth >= 1280 ? "split" : "write";
}

const today = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

/** A thesis: the user's Markdown about a company, with the workspace's charts, comps, screens
 * and models embedded where they put `[[embed:…]]` lines. Write / Preview / Side by side;
 * Save (Ctrl+S); Import and Export .md; Check my thesis (the assistant checks the saved text,
 * results beside it, with a history of checks). Owner-only like every saved item. */
export function ThesisPage(props: Props) {
  const { token, itemId, itemsVersion = 0 } = props;
  const [loaded, setLoaded] = useState<Loaded>({ state: "loading" });
  const [retry, setRetry] = useState(0);

  // the workspace's items again (a chart pinned from another step): the editor keeps its text
  useEffect(() => {
    if (!itemsVersion || loaded.state !== "ok") return;
    let live = true;
    fetchWorkspace(token, loaded.item.workspace_id)
      .then((ws) => live && setLoaded((l) => (l.state === "ok" ? { ...l, items: embeddable(ws.items) } : l)))
      .catch(() => undefined); // the embeds already shown stay
    return () => {
      live = false;
    };
    // loaded is read for the workspace id only
  }, [token, itemsVersion]);

  useEffect(() => {
    let live = true;
    openItem(token, itemId)
      .then(async (item) => {
        if (item.kind !== "thesis") throw new Error("not a thesis");
        // the workspace's other items, with their states: what the thesis can embed
        const ws = await fetchWorkspace(token, item.workspace_id);
        if (live) setLoaded({ state: "ok", item, thesis: restoreThesis(item.state), items: embeddable(ws.items) });
      })
      .catch((e) => live && setLoaded({
        state: "error",
        message: e instanceof Error && e.message === "not a thesis" ? "This item isn't a thesis." : failureReason(e),
        notFound: e instanceof ApiError && e.status === 404,
      }));
    return () => {
      live = false;
    };
  }, [token, itemId, retry]);

  const wrap = props.embedded ? "thesis-page embedded" : "page";
  if (loaded.state === "loading") return <div className={wrap}><Skeleton lines={8} label="Loading the thesis" /></div>;
  if (loaded.state === "error") {
    return (
      <div className={wrap}>
        {loaded.notFound ? (
          <p className="empty">This thesis doesn't exist, or isn't yours. Theses are visible only to their owner.</p>
        ) : (
          <ErrorPanel what="the thesis" detail={loaded.message} onRetry={() => setRetry((r) => r + 1)} />
        )}
      </div>
    );
  }
  return <ThesisEditor key={loaded.item.item_id} {...props} item={loaded.item} initial={loaded.thesis} items={loaded.items} />;
}

/** The workspace's items a thesis can embed, with their states. */
function embeddable(items: { item_id: string; kind: EmbedItem["kind"]; name: string; state: Record<string, unknown> | null }[]): EmbedItem[] {
  return items.flatMap((i) => (EMBEDDABLE.has(i.kind) && i.state ? [{ item_id: i.item_id, kind: i.kind, name: i.name, state: i.state }] : []));
}

const savedPrint = (name: string, t: Thesis) => JSON.stringify({ name, state: thesisState(t) });

function ThesisEditor({ token, item, initial, items, saved, label, readable, onOpenChart, user = null, embedded = false, active = true, bridge, onStatus, checks: shared }: Props & {
  item: SavedItemFull;
  initial: Thesis;
  items: EmbedItem[];
}) {
  const id = useId();
  const key = user ? draftKey(user, item.item_id) : null;
  // unsaved changes kept in this browser (StoredDraft): restored as they were when they were
  // made on top of the version saved now; offered otherwise
  const [draft] = useState<StoredDraft | null>(() => {
    const d = key ? restoreDraft(load(key)) : null;
    if (!d) return null;
    const r = restoreThesis({ companyId: d.companyId, markdown: d.markdown });
    return savedPrint(d.name.trim(), r) === savedPrint(item.name, initial) ? null : { ...d, markdown: r.markdown, companyId: r.companyId };
  });
  const savedFirst = savedPrint(item.name, initial);
  const resume = draft !== null && draft.base === savedFirst;
  const [name, setName] = useState(resume ? draft.name : item.name);
  const [companyId, setCompanyId] = useState(resume ? draft.companyId : initial.companyId);
  const [companyName, setCompanyName] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);
  const [markdown, setMarkdown] = useState(resume ? draft.markdown : initial.markdown);
  const [savedAs, setSavedAs] = useState(savedFirst);
  const [offered, setOffered] = useState<{ draft: StoredDraft; resumed: boolean } | null>(draft ? { draft, resumed: resume } : null);
  const [mode, setModeState] = useState<Mode>(storedMode);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ text: string; undo?: { label: string; to: { name: string; companyId: string | null; markdown: string } }; error?: boolean } | null>(null);
  // M5: the versions earlier saves replaced (app DB 014), read when the list is opened
  const [versions, setVersions] = useState<{ state: "loading" } | { state: "ok"; list: ItemVersion[] } | { state: "error"; message: string } | null>(null);
  // #62: where the user put the cursor; until they do, an embed goes at the end, not above the title
  const placed = useRef(false);
  const text = useRef<HTMLTextAreaElement>(null);
  const blocks = useRef<BlockEditorHandle>(null);
  const file = useRef<HTMLInputElement>(null);
  const cursor = useRef<number | null>(null);
  const hasTtm = useHasTtm(token);
  const own = useThesisCheck(token, shared ? null : item.item_id);
  const checks = shared ?? own;
  // Revisit: the company's newest 10-K/10-Q vs the last check (thesis/revisit.ts)
  const newer = useNewerFiling(token, checks.history[0], companyId);
  const [hash, setHash] = useState<string | null>(null);
  const [highlight, setHighlight] = useState<{ quote: string; n: number } | null>(null);

  const thesis: Thesis = { companyId, markdown };
  const dirty = savedPrint(name.trim(), thesis) !== savedAs;
  const written = hasOwnText(markdown);

  // the draft follows the text: kept while there are unsaved changes, dropped once saved
  useEffect(() => {
    if (!key) return;
    if (!dirty) {
      remove(key);
      return;
    }
    const t = window.setTimeout(() => {
      const d: StoredDraft = { name, markdown, companyId, base: savedAs, at: new Date().toISOString() };
      store(key, JSON.stringify(d));
    }, 400);
    return () => window.clearTimeout(t);
  }, [key, dirty, name, markdown, companyId, savedAs]);

  const savedMarkdown = useMemo(() => {
    try {
      return String((JSON.parse(savedAs) as { state: { markdown?: string } }).state.markdown ?? "");
    } catch {
      return "";
    }
  }, [savedAs]);
  useEffect(() => {
    onStatus?.({ itemId: item.item_id, dirty, saved: savedMarkdown });
  }, [dirty, savedMarkdown]);

  // the project's handle: pins go into the text being edited (latest values through refs)
  const latest = useRef({ markdown, dirty, name, companyId });
  latest.current = { markdown, dirty, name, companyId };
  useEffect(() => {
    if (!bridge) return;
    const handle: ThesisHandle = {
      itemId: item.item_id,
      pin: async (target, block) => {
        const cur = latest.current;
        const next = placePin(cur.markdown, target, block);
        if (next.length > MAX_MARKDOWN) throw new Error(`A thesis holds up to ${MAX_MARKDOWN.toLocaleString("en-US")} characters.`);
        setMarkdown(next);
        if (cur.dirty) return "draft";
        const saving = { companyId: cur.companyId, markdown: next };
        const res = await saved.update(item.item_id, { state: thesisState(saving) });
        setSavedAs(savedPrint(res.name, restoreThesis(res.state)));
        return "saved";
      },
      markdown: () => latest.current.markdown,
      showQuote: (q) => showQuoteRef.current(q),
    };
    bridge.current = handle;
    return () => {
      if (bridge.current === handle) bridge.current = null;
    };
  }, [bridge, item.item_id, saved]);
  const { allowance, refresh: refreshAllowance } = useAllowanceContext();
  // a check ran: it counted, several times over
  useEffect(() => {
    if (checks.run.status !== "running") refreshAllowance();
  }, [checks.run.status, refreshAllowance]);
  const nameError = nameProblem(name, ITEM_NAME_MAX);
  const directory = useDirectory(token);
  const named = companyId ? directory.company(companyId) : null;
  const company = companyId ? (named ? directory.label(companyId) : companyName) : null;
  const missing = useMemo(() => {
    const known = new Set(items.map((i) => i.item_id));
    return new Set(embeddedIds(markdown).filter((x) => !known.has(x))).size;
  }, [markdown, items]);

  // what the shown check's text_hash is compared with: the text as it is now
  useEffect(() => {
    let live = true;
    textHash(companyId, markdown)
      .then((h) => live && setHash(h))
      .catch(() => live && setHash(null));
    return () => {
      live = false;
    };
  }, [companyId, markdown]);
  const stale = !!checks.shown && hash !== null && checks.shown.text_hash !== hash;
  const checking = checks.run.status === "running";
  const showChecks = checks.history.length > 0 || checks.run.status !== "idle";

  /** A claim's quote: highlighted in the preview when it shows, else selected in the editor. */
  const showQuoteRef = useRef<(q: string) => void>(() => undefined);
  const showQuote = (quote: string) => {
    if (mode === "preview" || mode === "split") {
      setHighlight((h) => ({ quote, n: (h?.n ?? 0) + 1 }));
      return;
    }
    selectQuote(quote);
  };
  showQuoteRef.current = showQuote;
  const selectQuote = (quote: string) => {
    const at = findQuote(markdown, quote);
    if (!at) return;
    if (mode !== "markdown") {
      if (mode === "preview") setMode("write");
      window.setTimeout(() => blocks.current?.select(at.start, at.end), 0);
      return;
    }
    if (!text.current) return;
    text.current.focus();
    text.current.setSelectionRange(at.start, at.end);
  };

  const setMode = (m: Mode) => {
    setModeState(m);
    store(MODE_KEY, m);
  };

  const save = async () => {
    if (nameError || busy || !dirty) return;
    setBusy(true);
    try {
      const next = await saved.update(item.item_id, { name, state: thesisState(thesis) });
      setName(next.name);
      setSavedAs(savedPrint(next.name, restoreThesis(next.state)));
      setNotice(null);
      setOffered(null);
    } catch (e) {
      setNotice({ text: `Couldn't save: ${failureReason(e)}.`, error: true });
    } finally {
      setBusy(false);
    }
  };
  useEditorKeys(dirty, () => void save(), active);

  const discardDraft = () => {
    setName(item.name);
    setCompanyId(initial.companyId);
    setMarkdown(initial.markdown);
    setOffered(null);
    if (key) remove(key);
  };
  const restoreOffered = (d: StoredDraft) => {
    setName(d.name);
    setCompanyId(d.companyId);
    setMarkdown(d.markdown);
    setOffered({ draft: d, resumed: true });
  };

  // after an insert, put the cursor after the marker
  useEffect(() => {
    if (cursor.current === null || !text.current) return;
    text.current.focus();
    text.current.setSelectionRange(cursor.current, cursor.current);
    cursor.current = null;
  }, [markdown]);

  const embed = (itemId: string) => {
    if (!itemId) return;
    // #62: until the user puts the cursor somewhere, an embed goes at the end
    const at = mode === "markdown" ? (placed.current && text.current ? text.current.selectionStart : markdown.length)
      : mode === "preview" ? markdown.length
      : blocks.current?.cursor() ?? markdown.length;
    const r = insertEmbed(markdown, at, itemId);
    if (r.text.length > MAX_MARKDOWN) {
      setNotice({ text: `A thesis holds up to ${MAX_MARKDOWN.toLocaleString("en-US")} characters.`, error: true });
      return;
    }
    if (mode === "markdown") {
      placed.current = true; // the cursor goes after the marker
      cursor.current = r.cursor;
    }
    setMarkdown(r.text);
    if (mode === "preview") setMode("write");
  };
  const removed = (what: string, before: string) =>
    setNotice({ text: `Removed the ${what}. Save to keep it that way.`, undo: { label: "Undo", to: { name, companyId, markdown: before } } });

  const onFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    if (f.size > MAX_IMPORT_BYTES) {
      setNotice({ text: `${f.name} is too large to import (over ${Math.round(MAX_IMPORT_BYTES / 1000)} KB).`, error: true });
      return;
    }
    let raw: string;
    try {
      raw = await readText(f);
    } catch {
      setNotice({ text: `Couldn't read ${f.name}.`, error: true });
      return;
    }
    const r = importMarkdown(raw);
    if (!r.ok) {
      setNotice({ text: r.error, error: true });
      return;
    }
    const before = { name, companyId, markdown };
    setMarkdown(r.markdown);
    if (r.title && /^untitled/i.test(name.trim())) setName(r.title);
    setNotice({ text: `Imported ${f.name}. Save to keep it.`, ...(before.markdown ? { undo: { label: "Undo import", to: before } } : {}) });
  };

  // M6: embeds say what they show and the last check comes along (stale or not); #41: both
  // import back (embeds as their lines, the check dropped)
  const exportMd = () => {
    const md = thesisMarkdown({
      title: name.trim() || "Thesis", thesis, company, date: today(), items,
      describe: (i) => embedSummary(i, label),
      check: checks.shown ? checkSection(checks.shown, stale, label) : null,
    });
    download(`${slug(name.trim() || "thesis")}.md`, new Blob([md], { type: "text/markdown" }));
  };

  const showVersions = () => {
    if (versions) {
      setVersions(null);
      return;
    }
    setVersions({ state: "loading" });
    itemVersions(token, item.item_id)
      .then((r) => setVersions({ state: "ok", list: r.versions }))
      .catch((e) => setVersions({ state: "error", message: failureReason(e) }));
  };
  const restoreVersion = async (v: ItemVersion) => {
    try {
      const got = await itemVersion(token, item.item_id, v.version_id);
      const r = restoreThesis(got.state);
      const before = { name, companyId, markdown };
      setName(got.name);
      setCompanyId(r.companyId);
      setMarkdown(r.markdown);
      setVersions(null);
      setNotice({ text: `Restored the version saved ${when(v.saved_at)} (${r.markdown.length.toLocaleString("en-US")} characters). Save to keep it.`, undo: { label: "Undo restore", to: before } });
    } catch (e) {
      setNotice({ text: `Couldn't read that version: ${failureReason(e)}.`, error: true });
    }
  };

  const embeddable = items.filter((i) => i.item_id !== item.item_id);
  const editor = (
    <div className="thesis-write">
      <div className="row wrap thesis-tools">
        <label className="visually-hidden" htmlFor={`${id}-embed`}>Embed a saved item</label>
        <select id={`${id}-embed`} className="field narrow" value="" disabled={!embeddable.length}
          title={embeddable.length ? "Inserts an embed line at the cursor" : "Save a chart, comps table, screen or model in this workspace first"}
          onChange={(e) => embed(e.target.value)}>
          <option value="">{embeddable.length ? "Embed a saved item…" : "Nothing to embed yet"}</option>
          {embeddable.map((i) => (
            <option key={i.item_id} value={i.item_id}>{KIND_LABELS[i.kind]} · {i.name}</option>
          ))}
        </select>
        <span className="grow" />
        <span className={markdown.length > MAX_MARKDOWN * 0.95 ? "editor-warn small" : "faint small"}>
          {markdown.length.toLocaleString("en-US")} / {MAX_MARKDOWN.toLocaleString("en-US")}
        </span>
      </div>
      {mode !== "markdown" ? (
        <BlockEditor markdown={markdown} onChange={setMarkdown} onRemoved={removed} items={items} token={token} label={label}
          readable={readable} hasTtm={hasTtm} onOpenChart={onOpenChart} handle={blocks}
          placeholder={"# The thesis in one line\n\nWhy now, what the filings show, what would prove it wrong.\n\nPick “Embed a saved item” to draw a chart or model here."} />
      ) : (<>
      <label className="visually-hidden" htmlFor={`${id}-md`}>Thesis, in Markdown</label>
      <textarea
        id={`${id}-md`}
        ref={text}
        className="field thesis-text"
        value={markdown}
        maxLength={MAX_MARKDOWN}
        spellCheck
        placeholder={"# The thesis in one line\n\nWhy now, what the filings show, what would prove it wrong.\n\nPick “Embed a saved item” to draw a chart or model here."}
        onChange={(e) => {
          placed.current = true;
          setMarkdown(e.target.value);
        }}
        onMouseUp={() => {
          placed.current = true;
        }}
        onKeyUp={(e) => {
          if (e.key !== "Tab") placed.current = true; // tabbing in places nothing
        }}
      />
      </>)}
      <p className="faint small">
        {mode === "markdown"
          ? "The text as saved: # headings, **bold**, lists, tables, links. An embed is a line of its own; items from other workspaces aren't drawn."
          : "Markdown in the boxes: # headings, **bold**, lists, links. Charts, comps, models and quotes are cards: ↑ ↓ move one past the paragraph beside it (Alt+↑ / Alt+↓ on the card), ✕ removes it. Markdown shows the text as saved."}
        {missing > 0 && <span className="editor-warn"> {missing} embed{missing === 1 ? " points" : "s point"} at an item no longer in this workspace (skipped).</span>}
      </p>
    </div>
  );
  const preview = (
    <ThesisBody markdown={markdown} items={items} label={label} token={token} readable={readable} hasTtm={hasTtm} onOpenChart={onOpenChart}
      highlight={highlight} onNotShown={(q) => selectQuote(q)} />
  );

  return (
    <div className={embedded ? "thesis-page embedded" : "page thesis-page"}>
      <div className="row wrap editor-bar no-print">
        {!embedded && (
          <a className="btn sm ghost" href={href({ view: "workspace", workspaceId: item.workspace_id })}>
            ← {item.workspace_name ?? "Workspace"}
          </a>
        )}
        <span className="grow" />
        <span className={dirty ? "dirty small" : "faint small"} role="status">{dirty ? "● unsaved changes" : "saved"}</span>
        <input ref={file} type="file" hidden accept=".md,.markdown,.txt,text/markdown,text/plain" onChange={(e) => void onFile(e)} />
        <button className="btn sm" onClick={() => file.current?.click()} title="Replace the text with a .md or .txt file (not saved until you Save)">
          Import .md
        </button>
        <button className="btn sm" onClick={exportMd}
          title="Download as Markdown: embeds say what they show and import back as embeds; the last check comes along">
          Export .md
        </button>
        <button className="btn sm" onClick={showVersions} aria-expanded={!!versions} title="Earlier saved versions of this thesis">
          History
        </button>
        <button className="btn sm" onClick={checks.start} disabled={dirty || checking || !written}
          title={dirty ? "Save first: the check reads the saved thesis" : !written ? "Write the thesis first: headings and guidance alone have nothing to check"
            : `The assistant checks each factual claim and your models' assumptions against the filings (counts as 4 questions${allowance ? `; you have ${leftText(allowance)}` : ""})`}>
          {checking ? "Checking…" : "Check my thesis"}
        </button>
        <button className="btn primary" onClick={() => void save()} disabled={!dirty || busy || !!nameError} title="Save (Ctrl+S)">
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
      {notice && (
        <div className={notice.error ? "report-status error" : "report-status"} role={notice.error ? "alert" : "status"}>
          {notice.text}{" "}
          {notice.undo !== undefined && (
            <button className="btn sm ghost" onClick={() => {
              const to = notice.undo!.to;
              setName(to.name);
              setCompanyId(to.companyId);
              setMarkdown(to.markdown);
              setNotice(null);
            }}>
              {notice.undo.label}
            </button>
          )}
          <button className="btn sm ghost" onClick={() => setNotice(null)}>Dismiss</button>
        </div>
      )}
      {versions && (
        <section className="card versions no-print" aria-label="Earlier versions">
          <div className="card-h">
            <h3>Earlier versions</h3>
            <span className="sub">each save keeps the one it replaced, the newest 30</span>
            <span className="grow" />
            <button className="btn sm ghost" onClick={() => setVersions(null)}>Close</button>
          </div>
          {versions.state === "loading" ? <p className="faint small">Loading…</p>
            : versions.state === "error" ? <p className="err small" role="alert">Couldn't list them: {versions.message}.</p>
            : !versions.list.length ? <p className="empty">No earlier versions yet: each save from now keeps the one it replaces.</p>
            : (
              <ol className="version-list">
                {versions.list.map((v) => (
                  <li key={v.version_id}>
                    <span className="grow">
                      Saved {when(v.saved_at)}
                      <span className="faint"> · {v.characters?.toLocaleString("en-US") ?? "?"} characters · {v.name}</span>
                    </span>
                    <button className="btn sm" onClick={() => void restoreVersion(v)}
                      title="Put this version in the editor; nothing is saved until you Save">
                      Restore
                    </button>
                  </li>
                ))}
              </ol>
            )}
        </section>
      )}
      {offered && (
        <div className="report-status draft-status no-print" role="status">
          {offered.resumed
            ? `Restored your unsaved changes from ${when(offered.draft.at)}. `
            : `You have unsaved changes from ${when(offered.draft.at)}, made before this thesis was last saved. `}
          {!offered.resumed && (
            <button className="btn sm" onClick={() => restoreOffered(offered.draft)}>Restore them</button>
          )}{" "}
          <button className="btn sm ghost" onClick={discardDraft}>{offered.resumed ? "Discard them" : "Discard"}</button>
          {offered.resumed && (
            <button className="btn sm ghost" onClick={() => setOffered(null)}>Keep editing</button>
          )}
        </div>
      )}
      {newer && !checking && (
        <div className="report-status revisit no-print" role="status">
          New {newer.form} filed {day(newer.filed, true)} since your last check — re-check?{" "}
          <button className="btn sm" onClick={checks.start} disabled={dirty}
            title={dirty ? "Save first: the check reads the saved thesis" : "Check the thesis again against the new filing (counts as 4 questions)"}>
            Re-check
          </button>
        </div>
      )}
      <div className="editor-title">
        <label className="visually-hidden" htmlFor={`${id}-name`}>Title</label>
        <input id={`${id}-name`} className="field title-input" value={name} maxLength={ITEM_NAME_MAX} aria-invalid={!!nameError}
          onChange={(e) => setName(e.target.value)} />
        {nameError && <p className="err small" role="alert">{nameError}</p>}
        {!embedded && <div className="row wrap small">
          <span className="muted">Company:</span>
          {companyId && !picking ? (
            <>
              <a href={href({ view: "company", companyId })}>{company}</a>
              <button className="btn sm ghost" onClick={() => setPicking(true)}>Change</button>
              <button className="btn sm ghost" onClick={() => {
                setCompanyId(null);
                setCompanyName(null);
              }}>Remove</button>
            </>
          ) : picking || !companyId ? (
            <>
              <CompanySearch token={token} label="Company this thesis is about" placeholder="Optional: a company or ticker"
                exclude={companyId ? [companyId] : []}
                onPick={(cid, n) => {
                  setCompanyId(cid);
                  setCompanyName(n);
                  setPicking(false);
                }} />
              {picking && <button className="btn sm ghost" onClick={() => setPicking(false)}>Cancel</button>}
            </>
          ) : null}
        </div>}
      </div>
      <div className="seg no-print" role="group" aria-label="View">
        {MODES.map(([m, l, t]) => (
          <button key={m} className={mode === m ? "on" : undefined} aria-pressed={mode === m} title={t} onClick={() => setMode(m)}>
            {l}
          </button>
        ))}
      </div>
      <div className={showChecks ? "thesis-main with-check" : "thesis-main"}>
        <div className={`thesis-panes ${mode}`}>
          {mode !== "preview" && editor}
          {mode !== "write" && <div className="thesis-preview card">{preview}</div>}
        </div>
        {showChecks && <CheckPanel checks={checks} stale={stale} onQuote={showQuote} label={label} />}
      </div>
    </div>
  );
}

/** The thesis rendered: its Markdown through markdown.ts (DOMPurify) and its embeds drawn from
 * this workspace's items, reading their data now as the viewer. */
export function ThesisBody({ markdown, items, label, token, readable, hasTtm, onOpenChart, highlight, onNotShown }: {
  markdown: string;
  items: EmbedItem[];
  label: (companyId: string) => string;
  token: string;
  readable: Readable;
  hasTtm: (metric: string) => boolean;
  onOpenChart: (s: ChartState) => void;
  /** a check's quote to mark in the rendered text (n changes on every click) */
  highlight?: { quote: string; n: number } | null;
  /** the quote isn't in the rendered text (e.g. it spans Markdown syntax) */
  onNotShown?: (quote: string) => void;
}) {
  const parts = useMemo(() => thesisSegments(markdown, items), [markdown, items]);
  const doc = useRef<HTMLElement>(null);
  useEffect(() => {
    const root = doc.current;
    if (!root) return;
    const texts = [...root.querySelectorAll<HTMLElement>(".report-text")];
    texts.forEach(clearHighlights);
    if (!highlight) return;
    const mark = texts.reduce<HTMLElement | null>((found, t) => found ?? highlightQuote(t, highlight.quote), null);
    if (mark) mark.scrollIntoView?.({ block: "center", behavior: "smooth" });
    else onNotShown?.(highlight.quote);
  }, [highlight]);
  if (!parts.length) return <p className="empty">Nothing written yet.</p>;
  return (
    <article ref={doc} className="report-doc thesis-doc" aria-label="Thesis preview">
      {parts.map((p, i) =>
        p.type === "text" ? (
          <Markdown key={i} markdown={p.markdown} />
        ) : p.item.kind === "report" || p.item.kind === "thesis" ? null : (
          <Embed key={p.item.item_id} token={token} item={{ ...p.item, kind: p.item.kind, ref: p.item.item_id }} label={label}
            readable={readable} hasTtm={hasTtm} onOpenChart={onOpenChart} />
        ),
      )}
    </article>
  );
}

function Markdown({ markdown }: { markdown: string }) {
  const html = useMemo(() => renderMarkdown(markdown), [markdown]);
  return <div className="answer report-text" dangerouslySetInnerHTML={{ __html: html }} />;
}

/** "3 Nov, 14:05" for a draft's time. */
function when(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "earlier" : d.toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

/** A picked file's text, read in the browser as UTF-8 (nothing is uploaded). */
function readText(f: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result ?? ""));
    r.onerror = () => reject(r.error);
    r.readAsText(f, "utf-8");
  });
}

/** Which metrics have a TTM view (embedded charts ask for it), from /v1/screener/columns. */
function useHasTtm(token: string): (metric: string) => boolean {
  const [hasTtm, setHasTtm] = useState<(m: string) => boolean>(() => () => true);
  useEffect(() => {
    let live = true;
    screenerColumns(token)
      .then((r) => {
        const ttm = new Map(catalog(r.columns).map((m) => [m.name, m.ttm]));
        if (live) setHasTtm(() => (m: string) => ttm.get(m) ?? true);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [token]);
  return hasTtm;
}

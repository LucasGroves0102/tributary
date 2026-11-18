import { useEffect, useId, useState } from "react";

import { failureReason } from "../api";
import { useDirectory } from "../companies/directory";
import { Choices } from "../components/Choices";
import { CompanySearch } from "../components/CompanySearch";
import { toast } from "../components/Toasts";
import { localTime } from "../format";
import { href } from "../route";
import type { ColumnSpec } from "../views/model";
import type { ScreenWatch } from "../types";
import type { Watching } from "./useWatches";
import { fromThreshold, lastCheck, lineLabel, lineWords, toThreshold, TYPED_WORDS, typedAs } from "./watches";

/** A line as the form edits it: the typed value, in the column's typed unit. */
interface Draft {
  key: number;
  column: string;
  direction: "above" | "below";
  typed: string;
  companyId: string | null;
  /** "one": a company is (being) picked; "any": any company in the screen */
  scope: "any" | "one";
}

const DIRECTIONS: ["above" | "below", string][] = [["above", "Rises above"], ["below", "Falls below"]];

let seq = 0;

function drafts(w: ScreenWatch | null, columns: ColumnSpec[]): Draft[] {
  return (w?.lines ?? []).map((l) => {
    const spec = columns.find((c) => c.key === l.column);
    return {
      key: ++seq, column: l.column, direction: l.direction,
      typed: spec ? fromThreshold(spec.kind, l.threshold) : l.threshold,
      companyId: l.company_id, scope: l.company_id ? "one" : "any",
    };
  });
}

/** Under Companies' page bar, for the open saved screen: whether it is watched, its last check, and
 * the form that says what alerts (companies entering, leaving, a value crossing a line for one
 * company or any in it). The alerts job checks it once the nightly figures land, as the user. */
export function WatchControl({ token, itemId, name, watching, columns, shown, dirty }: {
  token: string;
  /** the open saved screen */
  itemId: string;
  name: string;
  watching: Watching;
  /** the screen's columns as the table shows them, own metrics included: what a line can use */
  columns: ColumnSpec[];
  /** the plain screener columns shown (views): saved with the watch */
  shown: string[];
  /** the page has changes the saved screen doesn't */
  dirty: boolean;
}) {
  const directory = useDirectory(token);
  const watch = watching.of(itemId);
  const limits = watching.list;
  const [open, setOpen] = useState(false);
  const [entering, setEntering] = useState(true);
  const [leaving, setLeaving] = useState(true);
  const [lines, setLines] = useState<Draft[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const formId = useId();

  // the form starts from the saved watch each time it opens (or another screen opens)
  useEffect(() => {
    if (!open) return;
    setEntering(watch ? watch.entering : true);
    setLeaving(watch ? watch.leaving : true);
    setLines(drafts(watch, columns));
    setError(null);
    // only when it opens or the screen changes
  }, [open, itemId]);
  useEffect(() => setOpen(false), [itemId]);

  if (!limits) return null; // loading, or no watches on this server
  const maxLines = limits.max_lines;
  const spec = (key: string) => columns.find((c) => c.key === key) ?? null;
  const status = lastCheck(watch, limits.max_companies);

  const patch = (key: number, p: Partial<Draft>) => setLines((ls) => ls.map((l) => (l.key === key ? { ...l, ...p } : l)));
  const problem = (): string | null => {
    if (!entering && !leaving && lines.length === 0) return "Choose companies entering, leaving, or add a line.";
    for (const l of lines) {
      const s = spec(l.column);
      if (!s) return "A line's column isn't in this screen any more: pick another.";
      if (toThreshold(s.kind, l.typed) === null) return `Give “${lineLabel(s)}” a number.`;
      if (l.scope === "one" && !l.companyId) return "Pick the company for that line, or choose any company.";
    }
    return null;
  };

  const save = async () => {
    const why = problem();
    if (why) return setError(why);
    setBusy(true);
    setError(null);
    try {
      await watching.save(itemId, {
        entering, leaving, columns: shown,
        lines: lines.map((l) => {
          const s = spec(l.column)!;
          return { column: l.column, label: lineLabel(s), direction: l.direction,
            threshold: toThreshold(s.kind, l.typed)!, company_id: l.scope === "one" ? l.companyId : null };
        }),
      });
      setOpen(false);
      toast({ text: watch ? `Saved the watch on “${name}”.` : `Watching “${name}”. The first check records who's in it; alerts follow from the next night's figures.`, topic: "watch" });
    } catch (e) {
      setError(`Couldn't save the watch: ${failureReason(e)}.`);
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    setBusy(true);
    setError(null);
    try {
      await watching.stop(itemId);
      setOpen(false);
      toast({ text: `Stopped watching “${name}”.`, topic: "watch" });
    } catch (e) {
      setError(`Couldn't stop watching: ${failureReason(e)}.`);
    } finally {
      setBusy(false);
    }
  };

  const atLimit = !watch && limits.watches.length >= limits.max_watches;
  return (
    <section className="watch-strip" aria-label={`Alerts for the saved screen ${name}`}>
      <div className="watch-row">
        <BellGlyph />
        {watch ? (
          <span className="watch-what">
            <b>Watching</b>{" "}
            {/* each line said, not "1 line" (Riley, test 10, finding 46) */}
            {listWords([watch.entering && "companies entering", watch.leaving && "companies leaving",
              ...watch.lines.map((l) => {
                const s = spec(l.column);
                const who = l.company_id ? `${directory.label(l.company_id)}'s ` : "";
                return s ? `${who}${lineWords(l, s.kind)}` : `${who}${l.label} ${l.direction} a line`;
              })].filter((x): x is string => !!x))}
            <span className="faint small">
              {" "}{status}{watch.checked_at ? ` Last checked ${localTime(watch.checked_at)}.` : ""}
            </span>
          </span>
        ) : (
          <span className="watch-what muted">Hear when companies enter or leave this screen, or a value crosses a line.</span>
        )}
        <span className="grow" />
        <button className="btn sm" aria-expanded={open} aria-controls={formId} onClick={() => setOpen((o) => !o)}
          disabled={atLimit} title={atLimit ? `You watch ${limits.max_watches} screens already` : undefined}>
          {watch ? "Change…" : "Watch…"}
        </button>
      </div>
      {open && (
        <div className="watch-form card" id={formId}>
          <p className="muted small">
            Checked once a day, after the nightly figures land, with your access: only companies whose figures you can
            read count. You hear about it under <a href={href({ view: "alerts" })}>Alerts</a> and at your{" "}
            <a href={href({ view: "settings" })}>webhooks (Settings)</a>. A value that reads as not meaningful, old or
            in another currency never crosses a line.
          </p>
          {dirty && (
            <p className="note-line small" role="note">
              This screen has unsaved changes: the watch checks “{name}” as saved. Save it to watch the changes.
            </p>
          )}
          <fieldset className="watch-kinds">
            <legend className="small muted">Tell me when</legend>
            <label className="chk"><input type="checkbox" checked={entering} onChange={(e) => setEntering(e.target.checked)} /> A company enters it</label>
            <label className="chk"><input type="checkbox" checked={leaving} onChange={(e) => setLeaving(e.target.checked)} /> A company leaves it</label>
          </fieldset>
          <ol className="watch-lines" aria-label="Lines">
            {lines.map((l) => {
              const s = spec(l.column);
              const typed = s ? typedAs(s.kind) : "plain";
              return (
                <li key={l.key} className="watch-line">
                  <select className="field" aria-label="Column" value={l.column}
                    onChange={(e) => patch(l.key, { column: e.target.value, typed: "" })}>
                    {!s && <option value={l.column}>A column no longer in this screen</option>}
                    {columns.map((c) => <option key={c.key} value={c.key}>{lineLabel(c)}</option>)}
                  </select>
                  <Choices label="Direction" value={l.direction} options={DIRECTIONS} set={(direction) => patch(l.key, { direction })} />
                  <span className="watch-value">
                    <input className="field" inputMode="decimal" aria-label={`Value${TYPED_WORDS[typed] ? ` in ${TYPED_WORDS[typed]}` : ""}`}
                      value={l.typed} placeholder={typed === "percent" ? "e.g. 25" : typed === "millions" ? "e.g. 500" : "e.g. 1.5"}
                      onChange={(e) => patch(l.key, { typed: e.target.value })} />
                    {TYPED_WORDS[typed] && <span className="faint small">{TYPED_WORDS[typed]}</span>}
                  </span>
                  <select className="field" aria-label="For" value={l.scope}
                    onChange={(e) => patch(l.key, { scope: e.target.value as Draft["scope"], companyId: null })}>
                    <option value="any">Any company in it</option>
                    <option value="one">One company</option>
                  </select>
                  {l.scope === "one" && (l.companyId ? (
                    <button type="button" className="chip removable" onClick={() => patch(l.key, { companyId: null })}
                      title="Pick another company">{directory.label(l.companyId)} ✕</button>
                  ) : (
                    <CompanySearch token={token} label="Company" placeholder="Name or ticker"
                      onPick={(companyId) => patch(l.key, { companyId })} />
                  ))}
                  <button type="button" className="btn sm ghost" onClick={() => setLines((ls) => ls.filter((x) => x.key !== l.key))}
                    aria-label={`Remove the line on ${s ? lineLabel(s) : "a column"}`}>Remove</button>
                  {s && toThreshold(s.kind, l.typed) !== null && (
                    <span className="faint small watch-reads">
                      Alerts when {l.scope === "one" && l.companyId ? directory.label(l.companyId) : "a company"}'s{" "}
                      {lineWords({ label: lineLabel(s), direction: l.direction, threshold: toThreshold(s.kind, l.typed)! }, s.kind)
                        .replace(/ (above|below) /, l.direction === "above" ? " rises above " : " falls below ")}
                    </span>
                  )}
                </li>
              );
            })}
          </ol>
          <div className="row wrap watch-actions">
            <button type="button" className="btn sm" disabled={lines.length >= maxLines || columns.length === 0}
              onClick={() => setLines((ls) => [...ls, { key: ++seq, column: columns[0]!.key, direction: "above", typed: "", companyId: null, scope: "any" }])}>
              Add a line
            </button>
            <span className="grow" />
            {watch && <button type="button" className="btn sm ghost" disabled={busy} onClick={() => void stop()}>Stop watching</button>}
            <button type="button" className="btn sm ghost" onClick={() => setOpen(false)}>Cancel</button>
            <button type="button" className="btn sm primary" disabled={busy} onClick={() => void save()}>
              {watch ? "Save" : "Start watching"}
            </button>
          </div>
          {error && <p className="err small" role="alert">{error}</p>}
        </div>
      )}
    </section>
  );
}

/** "a, b and c" */
function listWords(parts: string[]): string {
  return parts.length <= 1 ? parts.join("") : `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}`;
}

/** The unsaved screener's quiet line that a saved screen can be watched (Riley, test 10, finding 39: Watch
 * appeared only after a save, and nothing hinted that screens alert). Save is the page bar's. */
export function WatchHint() {
  return (
    <p className="watch-strip watch-hint">
      <BellGlyph />
      <span className="muted">Save this screen to watch it: you hear when companies enter or leave it, or a value crosses a line.</span>
    </p>
  );
}

function BellGlyph() {
  return (
    <svg className="watch-glyph" width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M8 1.5a4 4 0 0 0-4 4v2.6L2.6 11h10.8L12 8.1V5.5a4 4 0 0 0-4-4zM6.3 12.5a1.8 1.8 0 0 0 3.4 0"
        fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

/** Companies' list of the user's saved screens, each opening as the saved item; the watched
 * ones say so. Nothing when there are none. */
export function SavedScreens({ screens, watched, openId, onOpen }: {
  screens: { item_id: string; name: string }[];
  watched: ReadonlySet<string>;
  /** the saved screen the page has open */
  openId: string | null;
  onOpen: (itemId: string) => void;
}) {
  if (!screens.length) return null;
  return (
    <nav className="saved-screens" aria-label="Your saved screens">
      <span className="small muted">Your saved screens</span>
      <ul>
        {screens.map((s) => (
          <li key={s.item_id}>
            <button type="button" className={s.item_id === openId ? "screen-chip on" : "screen-chip"} aria-current={s.item_id === openId ? "true" : undefined}
              onClick={() => onOpen(s.item_id)} title={watched.has(s.item_id) ? `Open “${s.name}” (watched: you hear when it changes)` : `Open “${s.name}”`}>
              {s.name}
              {watched.has(s.item_id) && <>{" "}<span className="watched-mark">watched</span></>}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}

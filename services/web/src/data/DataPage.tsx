/** Your data (#/data, B2): the user's own datasets (CSV time series by company and date, query-api
 * datasets.py) and metrics (saved formulas, workspace items of kind `metric`). Only they can read
 * either; nothing here changes Tributary's own figures.
 *
 * Upload: the user picks a CSV; the browser reads its first 64 KB only to list the columns, so they
 * can say which holds the company, the date and (optionally) the publication date, and what each
 * value column is. "Check it" sends the file as a preview (nothing stored): the API's answer (rows,
 * companies, days, rows left out by number, or each problem by row and column) is what's shown.
 * "Store it" sends it again for real. The file itself is never kept in the browser.
 *
 * Metrics: a name, a formula (Tributary's metrics and mine.<dataset>.<field>) and the view it reads
 * in; saved into a workspace called "Your metrics". A metric is a screener column (Companies'
 * "Your metrics") and a chart formula. */

import { useCallback, useEffect, useMemo, useState, type ChangeEvent, type FormEvent } from "react";

import {
  ApiError,
  datasets as fetchDatasets,
  deleteDataset,
  failureReason,
  getItem,
  UploadRefused,
  uploadDataset,
  validateFormula,
} from "../api";
import { Choices } from "../components/Choices";
import { Folio } from "../components/Folio";
import { MenuButton } from "../components/MenuButton";
import { Empty, ErrorPanel, Skeleton } from "../components/States";
import { TableScroll } from "../components/TableScroll";
import { toast } from "../components/Toasts";
import { day, localDay } from "../format";
import { href } from "../route";
import type { Dataset, DatasetProblem, Datasets, DatasetUpload, FieldUnit, MetricState, SavedItem } from "../types";
import { ConfirmDelete } from "../workspaces/Actions";
import type { Saved } from "../workspaces/useSaved";
import {
  fieldRef,
  guessColumns,
  HEAD_BYTES,
  headerOf,
  knownWords,
  leftOutWords,
  problemWords,
  simplify,
  sizeWords,
  UNIT_WORDS,
  UNITS,
  unitsField,
  valueColumns,
} from "./dataset";

/** where new metrics are saved (found by name, made on first use) */
export const METRICS_WORKSPACE = "Your metrics";
/** /v1/formulas/validate needs a company for bare metrics; it reads no data, so any id does */
const CHECK_ONLY_COMPANY = "0000000000";

type Load = { state: "loading" } | { state: "ok"; data: Datasets } | { state: "off" } | { state: "error"; message: string };

interface Draft {
  file: File;
  header: string[];
  name: string;
  title: string;
  company: string;
  date: string;
  published: string;
  units: Record<string, FieldUnit>;
  currency: string;
  /** into one of the user's datasets, or a new one */
  into: Dataset | null;
  mode: "replace" | "append";
}

const rowsWords = (n: number) => `${n.toLocaleString("en-US")} ${n === 1 ? "row" : "rows"}`;

/** The file's first bytes as text (FileReader: every browser, and jsdom). */
function readHead(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("unreadable"));
    reader.readAsText(file.slice(0, HEAD_BYTES));
  });
}

export function DataPage({ token, saved }: { token: string; saved: Saved | null }) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);
  const refresh = useCallback(() => setAttempt((n) => n + 1), []);

  useEffect(() => {
    let live = true;
    fetchDatasets(token)
      .then((data) => live && setLoad(data ? { state: "ok", data } : { state: "off" }))
      .catch((e) => live && setLoad({ state: "error", message: failureReason(e) }));
    return () => {
      live = false;
    };
  }, [token, attempt]);

  const data = load.state === "ok" ? load.data : null;
  const [draftInto, setDraftInto] = useState<Dataset | null>(null);
  const [deleting, setDeleting] = useState<Dataset | null>(null);

  const remove = async (d: Dataset) => {
    setDeleting(null);
    try {
      await deleteDataset(token, d.dataset_id);
      toast({ text: `Deleted ${d.title}. Formulas and metrics that read mine.${d.name} now say there's no such dataset.` });
      refresh();
    } catch (e) {
      toast({ text: `Couldn't delete ${d.title}: ${failureReason(e)}.` });
    }
  };

  return (
    <div className="page data-page">
      <div className="row page-bar">
        <h1 className="grow">Your data</h1>
        {data && (
          <span className="muted">
            {data.datasets.length} of {data.limits.datasets} datasets, {sizeWords(data.datasets.reduce((s, d) => s + d.bytes, 0))} of{" "}
            {sizeWords(data.limits.user_bytes)}
          </span>
        )}
      </div>

      <Folio name="Datasets" lead line="Your own figures by company and date" aside="Only you can read them">
        {load.state === "loading" ? (
          <Skeleton lines={3} label="Loading your datasets" />
        ) : load.state === "error" ? (
          <ErrorPanel what="your datasets" detail={load.message} onRetry={refresh} />
        ) : load.state === "off" ? (
          <Empty>Your data isn't available on this server yet.</Empty>
        ) : !load.data.datasets.length ? (
          <Empty>
            No datasets yet. Upload a CSV below: a company column (tickers or CIKs), a date column and columns of numbers. Formulas then
            read each column as <code>mine.&lt;dataset&gt;.&lt;column&gt;</code>, beside Tributary's figures.
          </Empty>
        ) : (
          <TableScroll label="Your datasets">
            <table className="t data-table">
              <thead>
                <tr>
                  <th className="first">Dataset</th>
                  <th>Fields</th>
                  <th className="n">Companies</th>
                  <th className="n">Rows</th>
                  <th>Days</th>
                  <th>Uploaded</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {load.data.datasets.map((d) => (
                  <tr key={d.dataset_id}>
                    <td className="first">
                      <b>{d.title}</b>
                      <span className="data-name">mine.{d.name}</span>
                      <span className="data-name faint">{knownWords(d.known_from)}</span>
                    </td>
                    <td className="data-fields">
                      {d.fields.map((f) => (
                        <span key={f.name} className="data-field" title={`${fieldRef(d.name, f.name)}: ${UNIT_WORDS[f.unit]}${f.unit === "money" || f.unit === "per_share" ? ` (${d.currency})` : ""}`}>
                          {f.name}
                        </span>
                      ))}
                    </td>
                    <td className="n">{d.companies.toLocaleString("en-US")}</td>
                    <td className="n">{d.rows.toLocaleString("en-US")}</td>
                    <td>{d.first_day && d.last_day ? (d.first_day === d.last_day ? day(d.first_day, true) : `${day(d.first_day, true)} to ${day(d.last_day, true)}`) : "—"}</td>
                    <td>{localDay(d.uploaded_at, true)}</td>
                    <td>
                      <MenuButton label="More" count={false} items={[
                        { label: "Upload a newer file…", onSelect: () => setDraftInto(d) },
                        { label: "Delete…", onSelect: () => setDeleting(d) },
                      ]} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
        )}
      </Folio>

      {data && (
        <Folio name="Upload" line={draftInto ? `A newer file into ${draftInto.title}` : "A CSV of your own figures"}
          aside={`Up to ${sizeWords(data.limits.file_bytes)}, ${data.limits.rows.toLocaleString("en-US")} rows and ${data.limits.columns} columns`}>
          {data.uploads_allowed ? (
            <Upload token={token} data={data} into={draftInto} onCancelInto={() => setDraftInto(null)}
              onStored={() => { setDraftInto(null); refresh(); }} />
          ) : (
            <Empty>Your own data needs a permanent account: sign in with GitHub or with an account. A demo sandbox can't upload.</Empty>
          )}
        </Folio>
      )}

      <Metrics token={token} saved={saved} datasets={data?.datasets ?? []} />

      <Folio name="How it reads" line="Your data beside Tributary's">
        <ul className="data-howto small">
          <li>
            A formula reads a field as <code>mine.&lt;dataset&gt;.&lt;field&gt;</code> for the default company, or{" "}
            <code>NVDA.mine.&lt;dataset&gt;.&lt;field&gt;</code> for another: <code>revenue / mine.stores.count</code> is revenue per store.
          </li>
          <li>
            A date is the end of the period a value describes, placed in a quarter by the same rule as Tributary's figures; with several
            dates in one quarter the latest counts. A value is used as given, never summed into a trailing twelve months.
          </li>
          <li>
            As of a past day (the company page's As of), a row counts only from the day it was known: its publication date if you gave
            one, else the day you uploaded it. A newer file added to a dataset keeps the values it revises as earlier versions.
          </li>
          <li>Only you can read your datasets and metrics. Amounts in your data are in the dataset's currency; Tributary converts none.</li>
        </ul>
      </Folio>

      {deleting && (
        <ConfirmDelete text={`Delete ${deleting.title} and its ${deleting.rows.toLocaleString("en-US")} rows?`}
          detail="This can't be undone: Tributary keeps no other copy."
          onYes={() => void remove(deleting)} onNo={() => setDeleting(null)} />
      )}
    </div>
  );
}

function Upload({ token, data, into, onCancelInto, onStored }: {
  token: string;
  data: Datasets;
  into: Dataset | null;
  onCancelInto: () => void;
  onStored: () => void;
}) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [checked, setChecked] = useState<DatasetUpload | null>(null);
  const [problems, setProblems] = useState<{ list: DatasetProblem[]; count: number; hint: string | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // a newer file into a dataset starts a new draft
  useEffect(() => {
    setDraft((d) => (d ? { ...d, into, name: into?.name ?? d.name, title: into?.title ?? d.title } : d));
    setChecked(null);
    setProblems(null);
  }, [into]);

  const pick = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setError(null);
    setChecked(null);
    setProblems(null);
    if (file.size > data.limits.file_bytes) return setError(`That file is over ${sizeWords(data.limits.file_bytes)}: split it, or leave out columns you don't need.`);
    let head: string;
    try {
      head = await readHead(file);
    } catch {
      return setError("Couldn't read that file in the browser: try saving it again as CSV.");
    }
    const header = headerOf(head);
    if (!header.length) return setError("That file has no header row: the first row must name the columns.");
    const g = guessColumns(header);
    const base = file.name.replace(/\.[^.]*$/, "");
    setDraft({
      file, header, company: g.company, date: g.date, published: "",
      name: into?.name ?? simplify(base), title: into?.title ?? base.slice(0, 120),
      units: {}, currency: into?.currency ?? "USD", into, mode: "replace",
    });
  };

  const set = (patch: Partial<Draft>) => {
    setDraft((d) => (d ? { ...d, ...patch } : d));
    setChecked(null);
    setProblems(null);
  };

  const columns = useMemo(() => (draft ? valueColumns(draft.header, [draft.company, draft.date, draft.published]) : []), [draft]);
  const suggestion = draft ? guessColumns(draft.header).published : "";

  const send = async (dryRun: boolean) => {
    if (!draft) return;
    setBusy(true);
    setError(null);
    setProblems(null);
    const fields: Record<string, string> = {
      ...(draft.into ? { mode: draft.mode } : { name: draft.name }),
      title: draft.title.trim(),
      company_column: draft.company,
      date_column: draft.date,
      published_column: draft.published,
      units: unitsField(Object.fromEntries(Object.entries(draft.units).filter(([n]) => columns.some((c) => c.name === n)))),
      currency: draft.currency.trim().toUpperCase(),
    };
    try {
      const answer = await uploadDataset(token, draft.file, fields, { dryRun, datasetId: draft.into?.dataset_id });
      if (answer.preview) {
        setChecked(answer);
      } else {
        const left = leftOutWords(answer.left_out);
        toast({ text: `Stored ${rowsWords(answer.stored)} in ${answer.dataset.title}${answer.unchanged ? ` (${rowsWords(answer.unchanged)} unchanged, not stored again)` : ""}.${left ? ` ${left}` : ""}` });
        setDraft(null);
        setChecked(null);
        onStored();
      }
    } catch (e) {
      if (e instanceof UploadRefused) setProblems({ list: e.problems, count: e.count, hint: e.hint });
      else setError(`${dryRun ? "Couldn't check it" : "Couldn't store it"}: ${failureReason(e)}.`);
    } finally {
      setBusy(false);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void send(!checked);
  };

  return (
    <div className="data-upload">
      {into && (
        <p className="small">
          Uploading into <b>{into.title}</b> (mine.{into.name}).{" "}
          <button className="link" onClick={onCancelInto}>Upload a new dataset instead</button>
        </p>
      )}
      <p className="small import-lead">
        One row per company and date: a column of tickers or CIKs, a column of dates as YYYY-MM-DD, and columns of numbers. Nothing
        is stored until you check the file and store it; a row whose company Tributary can't match exactly is left out and listed.
      </p>
      <label className="btn import-file">
        {draft ? "Choose another file" : "Choose a CSV"}
        <input type="file" accept=".csv,.txt,text/csv" className="visually-hidden" onChange={(e) => void pick(e)} disabled={busy} />
      </label>
      {error && <p className="err small" role="alert">{error}</p>}

      {draft && (
        <form className="pf-form data-form" onSubmit={submit} aria-label="Upload a dataset">
          <p className="small faint">{draft.file.name}, {sizeWords(draft.file.size)}: {draft.header.length} columns</p>
          <div className="pf-fields">
            {!draft.into && (
              <label className="pf-field">
                <span>Formula name</span>
                <input className="field" value={draft.name} maxLength={40} required
                  onChange={(e) => set({ name: simplify(e.target.value) || e.target.value.toLowerCase() })} />
                <span className="faint small">mine.{draft.name || "…"}.&lt;field&gt;</span>
              </label>
            )}
            <label className="pf-field wide">
              <span>Title</span>
              <input className="field" value={draft.title} maxLength={120} onChange={(e) => set({ title: e.target.value })} />
            </label>
            {draft.into && (
              <div className="pf-field">
                <span>The new file</span>
                <Choices label="Replace or add" value={draft.mode} set={(mode) => set({ mode })}
                  options={[["replace", "Replaces its rows"], ["append", "Adds to them"]]} />
              </div>
            )}
          </div>
          <div className="pf-fields">
            <Column label="Company (ticker or CIK)" value={draft.company} header={draft.header} set={(company) => set({ company })} />
            <Column label="Date (YYYY-MM-DD)" value={draft.date} header={draft.header} set={(date) => set({ date })} />
            <Column label="Published on (optional)" value={draft.published} header={draft.header} none="None: known from today"
              set={(published) => set({ published })} />
            <label className="pf-field">
              <span>Currency of money</span>
              <input className="field data-currency" value={draft.currency} maxLength={3} onChange={(e) => set({ currency: e.target.value.toUpperCase() })} />
            </label>
          </div>
          {!draft.published && suggestion && (
            <p className="small faint">
              “{suggestion}” looks like a publication date. Choose it as Published on if each row should count only from that day.
            </p>
          )}
          {columns.length > 0 && (
            <fieldset className="pf-field full">
              <legend>The value columns and how formulas read them</legend>
              <div className="data-units">
                {columns.map((c) => (
                  <label key={c.name} className="data-unit">
                    <span>{c.label} <span className="faint">({c.name})</span></span>
                    <select className="field" value={draft.units[c.name] ?? "number"}
                      onChange={(e) => set({ units: { ...draft.units, [c.name]: e.target.value as FieldUnit } })}>
                      {UNITS.map(([u, words]) => <option key={u} value={u}>{words}</option>)}
                    </select>
                  </label>
                ))}
              </div>
            </fieldset>
          )}

          {problems && (
            <div className="errpanel" role="alert">
              <p><b>The file can't be stored as it is.</b> {problems.count === 1 ? "One problem" : `${problems.count.toLocaleString("en-US")} problems`}; nothing was stored.</p>
              <ul className="small">
                {problems.list.map((p, i) => <li key={i}>{problemWords(p)}</li>)}
              </ul>
              {problems.count > problems.list.length && <p className="small faint">And {(problems.count - problems.list.length).toLocaleString("en-US")} more.</p>}
              {problems.hint && <p className="small">{problems.hint}</p>}
            </div>
          )}

          {checked?.preview && (
            <div className="data-check" role="status">
              <p>
                <b>{rowsWords(checked.rows)}</b> for {checked.companies.toLocaleString("en-US")}{" "}
                {checked.companies === 1 ? "company" : "companies"}, {day(checked.first_day, true)} to {day(checked.last_day, true)};{" "}
                {knownWords(checked.known_from).toLowerCase()}.
              </p>
              <p className="small">
                Fields: {checked.fields.map((f) => `${fieldRef(checked.name || draft.name, f.name)} (${UNIT_WORDS[f.unit].toLowerCase()})`).join(", ")}.
              </p>
              {leftOutWords(checked.left_out) && <p className="small">{leftOutWords(checked.left_out)}</p>}
              {checked.blank_rows > 0 && <p className="small faint">{checked.blank_rows.toLocaleString("en-US")} rows with no value were skipped.</p>}
            </div>
          )}

          <div className="pf-inline">
            <span className="grow" />
            <button type="submit" className={checked ? "btn primary" : "btn"} disabled={busy || !draft.company || !draft.date || (!draft.into && !draft.name)}>
              {busy ? (checked ? "Storing…" : "Checking…") : checked?.preview ? `Store ${rowsWords(checked.rows)}` : "Check it"}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

function Column({ label, value, header, set, none }: {
  label: string;
  value: string;
  header: string[];
  set: (v: string) => void;
  none?: string;
}) {
  return (
    <label className="pf-field">
      <span>{label}</span>
      <select className="field" value={value} onChange={(e) => set(e.target.value)}>
        <option value="">{none ?? "Choose a column"}</option>
        {header.filter((h) => h).map((h, i) => <option key={`${i}-${h}`} value={h}>{h}</option>)}
      </select>
    </label>
  );
}

interface MetricRow extends SavedItem {
  state: MetricState | null;
}

function Metrics({ token, saved, datasets }: { token: string; saved: Saved | null; datasets: Dataset[] }) {
  const items = useMemo(() => saved?.list.state === "ok"
    ? saved.list.workspaces.flatMap((w) => w.items.filter((i) => i.kind === "metric"))
    : [], [saved?.list]);
  const [states, setStates] = useState<Record<string, MetricState | null>>({});
  const ids = items.map((i) => i.item_id).join(",");

  useEffect(() => {
    let live = true;
    for (const id of ids ? ids.split(",") : []) {
      getItem(token, id)
        .then((it) => live && setStates((s) => ({ ...s, [id]: it.state as unknown as MetricState })))
        .catch(() => live && setStates((s) => ({ ...s, [id]: null })));
    }
    return () => {
      live = false;
    };
  }, [token, ids]);

  const rows: MetricRow[] = items.map((i) => ({ ...i, state: states[i.item_id] ?? null }));

  return (
    <Folio name="Metrics" line="Your own formulas, as screener columns and on charts" aside="At most 20; five a screen">
      {saved === null || saved.list.state === "unavailable" ? (
        <Empty>Saving metrics needs saved work, which isn't available here.</Empty>
      ) : (
        <>
          {rows.length > 0 && (
            <TableScroll label="Your metrics">
              <table className="t data-table">
                <thead>
                  <tr>
                    <th className="first">Metric</th>
                    <th>Formula</th>
                    <th>Reads</th>
                    <th aria-label="Actions" />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((m) => (
                    <tr key={m.item_id}>
                      <td className="first"><b>{m.name}</b></td>
                      <td><code>{m.state?.formula ?? "…"}</code></td>
                      <td>{m.state ? (m.state.view === "quarter" ? "The latest quarter" : "Trailing twelve months") : ""}</td>
                      <td className="row">
                        <a className="btn sm" href={href({ view: "companies", screen: { metric: [m.item_id] } })}>Screen with it</a>
                        <button className="btn sm ghost" onClick={() => saved.deleteWithUndo("item", m.item_id)}>Delete</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScroll>
          )}
          <NewMetric token={token} saved={saved} datasets={datasets} count={rows.length} />
        </>
      )}
    </Folio>
  );
}

function NewMetric({ token, saved, datasets, count }: { token: string; saved: Saved; datasets: Dataset[]; count: number }) {
  const [name, setName] = useState("");
  const [formula, setFormula] = useState("");
  const [view, setView] = useState<"quarter" | "ttm">("ttm");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const example = datasets[0]?.fields[0] ? `revenue / ${fieldRef(datasets[0].name, datasets[0].fields[0].name)}` : "operating_income / revenue";

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (count >= 20) return setError("You can save at most 20 metrics: delete one first.");
    setBusy(true);
    try {
      const check = await validateFormula(token, { formula: formula.trim(), default_company: CHECK_ONLY_COMPANY, basis: "fiscal", view });
      if (!check.valid) {
        const first = check.errors[0];
        return setError(first ? `${first.message}${first.hint ? `. ${first.hint}` : ""}` : "That formula doesn't read.");
      }
      await saved.create("metric", name.trim(), { formula: formula.trim(), view }, { newWorkspace: METRICS_WORKSPACE });
      toast({ text: `Saved ${name.trim()}. Add it to the screener from Companies' Your metrics.` });
      setName("");
      setFormula("");
    } catch (err) {
      setError(`Couldn't save it: ${err instanceof ApiError ? failureReason(err) : "try again"}.`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="pf-form pf-inline data-metric-form" onSubmit={(e) => void submit(e)} aria-label="New metric">
      <label className="pf-field">
        <span>Name</span>
        <input className="field" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} required placeholder="Revenue per store" />
      </label>
      <label className="pf-field wide">
        <span>Formula</span>
        <input className="field" value={formula} onChange={(e) => setFormula(e.target.value)} maxLength={500} required placeholder={example}
          spellCheck={false} autoComplete="off" />
      </label>
      <div className="pf-field">
        <span>Reads</span>
        <Choices label="The period a column reads" value={view} set={setView} options={[["ttm", "TTM"], ["quarter", "Quarter"]]} />
      </div>
      <button type="submit" className="btn" disabled={busy || !name.trim() || !formula.trim()}>{busy ? "Saving…" : "Save metric"}</button>
      {error && <p className="err small" role="alert">{error}</p>}
    </form>
  );
}

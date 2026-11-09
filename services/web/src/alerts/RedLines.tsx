import { useEffect, useId, useState, type FormEvent } from "react";

import { addRedLine, ApiError, deleteRedLine, failureReason, redLines, validateFormula } from "../api";
import { ErrorPanel, Skeleton } from "../components/States";
import { day } from "../format";
import type { RedLineOp, RedLineResult, RedLines as RedLinesData } from "../types";
import { inDisplay, OP_WORDS, RED_LINE_WORDS, redLineState, ruleText, thresholdFromInput } from "./alerts";

/** Rules worth starting from: each a formula in the formula language (no ticker: the company's own
 * figures), its view, and the comparison that describes the trouble. */
export const PRESETS: { key: string; label: string; formula: string; view: "quarter" | "ttm"; op: RedLineOp; threshold: string }[] = [
  { key: "gm", label: "Gross margin below", formula: "gross_margin", view: "ttm", op: "<", threshold: "" },
  { key: "om", label: "Operating margin below", formula: "operating_margin", view: "ttm", op: "<", threshold: "" },
  { key: "growth", label: "Revenue growth (TTM, on a year earlier) below", formula: "revenue_growth", view: "ttm", op: "<", threshold: "" },
  { key: "dso", label: "Receivables growth over revenue growth by more than", formula: "(receivables / receivables[-4q] - 1) - (revenue / revenue[-4q] - 1)", view: "quarter", op: ">", threshold: "10" },
  { key: "fcf", label: "Free cash flow margin below", formula: "fcf_margin", view: "ttm", op: "<", threshold: "" },
  { key: "debt", label: "Debt to equity above", formula: "debt_to_equity", view: "quarter", op: ">", threshold: "" },
];

type Load = { state: "loading" } | { state: "ok"; data: RedLinesData; evaluated: boolean } | { state: "off" } | { state: "error"; message: string };

/** The Monitor step's red lines (F6): the user's rules on this company's figures, each evaluated
 * now on the latest figures (the Query API, as the viewer: financials needed), and evaluated again
 * by the alerts job on each new 10-K or 10-Q, the result in the alert. A red line describes the
 * trouble: "Crossed" means the trouble is there. */
export function RedLines({ token, companyId, name }: { token: string; companyId: string; name: string }) {
  const id = useId();
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [preset, setPreset] = useState(PRESETS[0]!.key);
  const chosen = PRESETS.find((p) => p.key === preset);
  const [formula, setFormula] = useState(PRESETS[0]!.formula);
  const [view, setView] = useState<"quarter" | "ttm">(PRESETS[0]!.view);
  const [op, setOp] = useState<RedLineOp>(PRESETS[0]!.op);
  const [threshold, setThreshold] = useState("");
  const [label, setLabel] = useState("");
  const [display, setDisplay] = useState<string | null>("percent");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    redLines(token, companyId, true)
      .then((data) => live && setLoad(data ? { state: "ok", data, evaluated: true } : { state: "off" }))
      .catch(async (e) => {
        // no financials on this plan: the rules still list, unevaluated
        if (e instanceof ApiError && e.status === 403) {
          const data = await redLines(token, companyId).catch(() => null);
          if (live) setLoad(data ? { state: "ok", data, evaluated: false } : { state: "error", message: failureReason(e) });
          return;
        }
        if (live) setLoad({ state: "error", message: failureReason(e) });
      });
    return () => {
      live = false;
    };
  }, [token, companyId, attempt]);

  // what the formula reads as (percent: the threshold is typed in %), and its problems, as typed
  useEffect(() => {
    if (!formula.trim()) return;
    let live = true;
    const t = window.setTimeout(() => {
      validateFormula(token, { formula, default_company: companyId, basis: "fiscal", view })
        .then((v) => {
          if (!live) return;
          if (v.valid) {
            setDisplay(v.display ?? null);
            setProblem(null);
          } else setProblem(v.errors.map((x) => x.message).join("; "));
        })
        .catch(() => undefined);
    }, 350);
    return () => {
      live = false;
      window.clearTimeout(t);
    };
  }, [token, companyId, formula, view]);

  const pick = (key: string) => {
    setPreset(key);
    const p = PRESETS.find((x) => x.key === key);
    if (!p) return;
    setFormula(p.formula);
    setView(p.view);
    setOp(p.op);
    setThreshold(p.threshold);
  };
  const stored = thresholdFromInput(threshold, display);
  const autoLabel = chosen && formula === chosen.formula
    ? `${chosen.label} ${threshold.trim()}${display === "percent" ? (chosen.key === "dso" ? " pts" : "%") : ""}`.trim()
    : `${formula} ${OP_WORDS[op]} ${threshold.trim()}`;
  const add = async (e: FormEvent) => {
    e.preventDefault();
    if (stored === null || problem) return;
    setBusy(true);
    setError(null);
    try {
      await addRedLine(token, { company_id: companyId, label: (label.trim() || autoLabel).slice(0, 120), formula: formula.trim(), view, op, threshold: stored });
      setLabel("");
      setThreshold(chosen?.threshold ?? "");
      setAttempt((n) => n + 1);
    } catch (err) {
      setError(failureReason(err));
    } finally {
      setBusy(false);
    }
  };
  const remove = async (ruleId: string) => {
    setError(null);
    try {
      await deleteRedLine(token, ruleId);
      setAttempt((n) => n + 1);
    } catch (err) {
      setError(failureReason(err));
    }
  };

  if (load.state === "loading") return <Skeleton lines={3} label="Loading your red lines" />;
  if (load.state === "off") return <p className="empty">Red lines aren't available on this server yet.</p>;
  if (load.state === "error") return <ErrorPanel what="your red lines" detail={load.message} onRetry={() => setAttempt((n) => n + 1)} />;
  const results = new Map((load.data.evaluation ?? []).map((r) => [r.rule_id, r]));
  const rules = load.data.red_lines;
  const full = rules.length >= load.data.max_per_company;
  return (
    <>
      {rules.length ? (
        <ul className="redline-list">
          {rules.map((rule) => {
            const r: RedLineResult | undefined = results.get(rule.rule_id);
            const state = r ? redLineState(r) : null;
            return (
              <li key={rule.rule_id} className={`redline ${state ?? "unknown"}`}>
                <span className={`redline-state ${state ?? "unknown"}`}>{state ? RED_LINE_WORDS[state] : "Not evaluated"}</span>
                <span className="grow">
                  <b>{rule.label}</b>{" "}
                  <span className="faint small">
                    {ruleText(rule)} · {rule.view === "ttm" ? "trailing twelve months" : "quarterly"}
                    {r?.status === "ok" && r.period_end ? ` · ${inDisplay(r.value, r.display, r.unit)} at ${day(r.period_end, true)}` : ""}
                  </span>
                </span>
                <button className="btn sm ghost" aria-label={`Remove the red line ${rule.label}`} onClick={() => void remove(rule.rule_id)}>Remove</button>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="empty">No red lines yet. Decide now what would worry you about {name}'s numbers; each new 10-K or 10-Q is checked against them.</p>
      )}
      {!load.evaluated && <p className="faint small">Your plan doesn't include {name}'s financials, so these can't be evaluated.</p>}
      {!full ? (
        <form className="redline-form" onSubmit={(e) => void add(e)} aria-label="Add a red line">
          <div className="row wrap">
            <label htmlFor={`${id}-preset`} className="faint small">Rule</label>
            <select id={`${id}-preset`} className="field" value={preset} onChange={(e) => pick(e.target.value)}>
              {PRESETS.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
              <option value="custom">A formula of your own…</option>
            </select>
          </div>
          <div className="row wrap">
            <label htmlFor={`${id}-formula`} className="faint small">Formula</label>
            <input id={`${id}-formula`} className="field grow mono" value={formula} maxLength={500} spellCheck={false}
              aria-invalid={!!problem} aria-describedby={`${id}-help`}
              onChange={(e) => {
                setFormula(e.target.value);
                setPreset("custom");
              }} />
            <label htmlFor={`${id}-view`} className="visually-hidden">Figures</label>
            <select id={`${id}-view`} className="field narrow" value={view} onChange={(e) => setView(e.target.value as "quarter" | "ttm")}>
              <option value="ttm">trailing 12 months</option>
              <option value="quarter">each quarter</option>
            </select>
          </div>
          <div className="row wrap">
            <label htmlFor={`${id}-op`} className="faint small">Crossed when it is</label>
            <select id={`${id}-op`} className="field narrow" value={op} onChange={(e) => setOp(e.target.value as RedLineOp)}>
              {(Object.keys(OP_WORDS) as RedLineOp[]).map((o) => <option key={o} value={o}>{OP_WORDS[o]}</option>)}
            </select>
            <label htmlFor={`${id}-threshold`} className="visually-hidden">Threshold{display === "percent" ? ", in percent" : ""}</label>
            <input id={`${id}-threshold`} className="field narrow" inputMode="decimal" value={threshold} placeholder={display === "percent" ? "e.g. 55" : "a number"}
              onChange={(e) => setThreshold(e.target.value)} aria-invalid={threshold.trim() !== "" && stored === null} />
            <span className="faint small">{display === "percent" ? (preset === "dso" ? "points (%)" : "%") : display === "multiple" ? "x" : ""}</span>
            <label htmlFor={`${id}-label`} className="visually-hidden">Name</label>
            <input id={`${id}-label`} className="field grow" maxLength={120} value={label} placeholder={autoLabel} onChange={(e) => setLabel(e.target.value)} />
            <button className="btn primary" type="submit" disabled={busy || stored === null || !!problem}>{busy ? "Adding…" : "Add red line"}</button>
          </div>
          <p id={`${id}-help`} className="faint small">
            {problem ? <span className="err">{problem}</span>
              : "Metrics from the catalog with + - * /, lags like revenue[-4q], ttm() and abs(); no ticker (it reads this company)."}
          </p>
        </form>
      ) : (
        <p className="faint small">At most {load.data.max_per_company} red lines per company.</p>
      )}
      {error && <p className="err small" role="alert">Couldn't save that: {error}.</p>}
    </>
  );
}

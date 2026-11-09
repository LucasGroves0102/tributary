import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

import { allowance as fetchAllowance } from "../api";
import type { Allowance } from "../types";

/** The rolling hour moves on by itself: look again now and then. */
const REFRESH_MS = 5 * 60_000;

const count = (n: number) => n.toLocaleString("en-US");

/** The questions left, in the tightest terms that apply: per hour always, per day when the account
 * has a daily cap. Reads agent-api `/v1/allowance`, which spends nothing. Null while unknown or on
 * an older agent-api: then nothing is claimed and the server's refusal says what it says. */
export function useAllowance(token: string | null, busy: boolean): { allowance: Allowance | null; refresh: () => void } {
  const [allowance, setAllowance] = useState<Allowance | null>(null);
  const live = useRef(token);
  live.current = token;
  const refresh = useCallback(() => {
    if (!token) return;
    fetchAllowance(token)
      .then((a) => {
        if (live.current === token) setAllowance(a);
      })
      .catch(() => undefined);
  }, [token]);

  useEffect(() => {
    if (!token) {
      setAllowance(null);
      return;
    }
    refresh();
    const id = window.setInterval(refresh, REFRESH_MS);
    return () => window.clearInterval(id);
  }, [token, refresh]);

  // an answer just finished: it counted
  const wasBusy = useRef(busy);
  useEffect(() => {
    if (wasBusy.current && !busy) refresh();
    wasBusy.current = busy;
  }, [busy, refresh]);

  return { allowance, refresh };
}

/** For what spends several at once (a report, a thesis check): what's left, and to look again. */
export const AllowanceContext = createContext<{ allowance: Allowance | null; refresh: () => void }>({
  allowance: null,
  refresh: () => undefined,
});

export function useAllowanceContext() {
  return useContext(AllowanceContext);
}

/** "28 left this hour, 998 today". */
export function leftText(a: Allowance): string {
  const hour = `${count(a.left_this_hour)} left this hour`;
  return a.left_today === null ? hour : `${hour}, ${count(a.left_today)} today`;
}

/** "28 of 30 questions left this hour · 998 of 1,000 today". */
export function meterText(a: Allowance): string {
  if (a.paused) return "The assistant is paused until midnight UTC: its daily budget is used up";
  const hour = `${count(a.left_this_hour)} of ${count(a.per_hour)} questions left this hour`;
  return a.left_today === null || a.per_day === null ? hour : `${hour} · ${count(a.left_today)} of ${count(a.per_day)} today`;
}

/** The tighter allowance, for the bar and whether to warn. */
export function tightest(a: Allowance): { left: number; of: number } {
  const hour = { left: a.left_this_hour, of: a.per_hour };
  if (a.left_today === null || a.per_day === null) return hour;
  return a.left_today < a.left_this_hour ? { left: a.left_today, of: a.per_day } : hour;
}

/** The limits in a sentence, for hovers and What Tributary covers. */
export function limitsText(a: Allowance | null): string {
  const each = a
    ? `A report counts as ${a.report_questions} questions and a thesis check as ${a.check_questions}`
    : "A report counts as 3 questions and a thesis check as 4";
  if (!a) return `The assistant is limited per hour and per day; its panel shows what you have left. ${each}.`;
  const day = a.per_day === null ? "" : ` and ${count(a.per_day)} a day (resets at midnight UTC)`;
  return `Your account can ask ${count(a.per_hour)} questions an hour (counted over the last 60 minutes)${day}, with ` +
    `${count(a.network_per_hour)} an hour from one network address. ${each}. The assistant also pauses for everyone if its ` +
    "daily budget runs out, until midnight UTC.";
}

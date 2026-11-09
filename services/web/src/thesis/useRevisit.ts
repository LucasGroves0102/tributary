import { useEffect, useState } from "react";

import { filingIndex, thesisChecks } from "../api";
import type { CheckFilingRef, IndexedFiling, ThesisCheckSummary } from "../types";
import { checkCompany, newerFiling, PERIODIC_FORMS } from "./revisit";

/** Each company's newest 10-K/10-Q (public filing index), asked once per page load and token:
 * a workspace with several theses about one company asks once. */
let owner: string | null = null;
const newest = new Map<string, Promise<IndexedFiling | null>>();

export function newestPeriodic(token: string, companyId: string): Promise<IndexedFiling | null> {
  if (owner !== token) {
    newest.clear();
    owner = token;
  }
  let p = newest.get(companyId);
  if (!p) {
    p = filingIndex(token, companyId, { forms: PERIODIC_FORMS, limit: 1 })
      .then((r) => r.filings[0] ?? null)
      .catch(() => {
        newest.delete(companyId); // asked again next time
        return null;
      });
    newest.set(companyId, p);
  }
  return p;
}

/** For tests. */
export function clearNewest(): void {
  newest.clear();
  owner = null;
}

/** The newer 10-K/10-Q since `check` (null: none, no check, no company, or not known yet). */
export function useNewerFiling(token: string, check: ThesisCheckSummary | null | undefined, companyId: string | null): CheckFilingRef | null {
  const [out, setOut] = useState<CheckFilingRef | null>(null);
  const company = check ? checkCompany(check, companyId) : null;
  useEffect(() => {
    setOut(null);
    if (!check || !company) return;
    let live = true;
    newestPeriodic(token, company).then((f) => live && setOut(newerFiling(check, company, f)));
    return () => {
      live = false;
    };
  }, [token, check?.check_id, company]);
  return out;
}

/** The workspace page's theses: per thesis item id, the newer 10-K/10-Q since its last check.
 * One checks-list request per thesis (at most `max`) and one index request per company. */
export function useNewerFilings(token: string, thesisIds: string[], max = 20): Record<string, CheckFilingRef> {
  const [out, setOut] = useState<Record<string, CheckFilingRef>>({});
  const key = thesisIds.slice(0, max).join(",");
  useEffect(() => {
    let live = true;
    setOut({});
    for (const id of key ? key.split(",") : []) {
      thesisChecks(token, id)
        .then(async (r) => {
          const last = r.checks[0];
          const company = last ? checkCompany(last, null) : null;
          if (!last || !company) return;
          const f = newerFiling(last, company, await newestPeriodic(token, company));
          if (f && live) setOut((o) => ({ ...o, [id]: f }));
        })
        .catch(() => undefined); // no notice; the thesis page still works
    }
    return () => {
      live = false;
    };
  }, [token, key]);
  return out;
}

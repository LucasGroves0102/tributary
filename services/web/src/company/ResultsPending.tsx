import { earnings } from "../api";
import { day } from "../format";
import { href } from "../route";
import { resultsPending } from "./events";
import { useLoad } from "./useLoad";

/** One quiet line when a quarter's results are out (its 8-K release) before its 10-Q or 10-K: the
 * figures here end at the last report until that one is filed (`events.ts` resultsPending). Asked
 * only of a reader of the company's filings (`/earnings` needs the tier: a refusal would be a failed
 * request on every load); an older API's 404 or any failure says nothing. */
export function ResultsPending({ token, companyId, fiscalYearEnd, slot = false }: {
  token: string;
  companyId: string;
  fiscalYearEnd: string | null;
  /** in a folio's notes (the Overview's figures): the seal's empty slot first, so it lines up with them */
  slot?: boolean;
}) {
  const load = useLoad(`${companyId}|results-pending`, () => earnings(token, companyId, 1, false).catch(() => null));
  const pending = load?.state === "ok" ? resultsPending(load.data, fiscalYearEnd) : null;
  if (!pending) return null;
  const read = href({ view: "filing", companyId, accessionNo: pending.accessionNo, section: pending.sectionKey });
  return (
    <p className="foot results-pending" role="note">
      {slot && <span className="seal-slot" aria-hidden="true" />}
      <span>
        Results for the quarter ended {day(pending.periodEnd, true)} were announced on {day(pending.announced)} (
        <a href={read}>8-K</a>). Detailed figures arrive with the {pending.form}.
      </span>
    </p>
  );
}

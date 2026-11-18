/** The company page's quiet line when the user holds the company: "You hold 1.75 shares, since
 * Nov 2, 2026. Your holding". Nothing when they don't, when it's sold in full, or from a Query API
 * without the portfolio (404, the deploy window). */

import { useEffect, useState } from "react";

import { heldPosition } from "../api";
import { href } from "../route";
import type { HeldPosition } from "../types";
import { heldSince, shares } from "./portfolio";

export function HoldingLine({ token, companyId, today = false }: {
  token: string;
  companyId: string;
  /** the page is as of a past day: the holding is today's, and says so (Riley, test 10, finding 18) */
  today?: boolean;
}) {
  const [held, setHeld] = useState<HeldPosition | null>(null);
  useEffect(() => {
    let live = true;
    setHeld(null);
    heldPosition(token, companyId)
      .then((p) => live && setHeld(p))
      .catch(() => undefined); // an optional line: nothing on a failure
    return () => {
      live = false;
    };
  }, [token, companyId]);
  if (!held?.open) return null;
  return (
    <p className="meta co-meta co-held">
      <span>
        {today ? "Today you hold" : "You hold"} {shares(held.shares)} {Number(held.shares) === 1 ? "share" : "shares"}, since {heldSince(held.first_bought, held.added_at)}.{" "}
        <a href={href({ view: "portfolio", holdingId: held.holding_id })}>Your holding</a>
      </span>
    </p>
  );
}

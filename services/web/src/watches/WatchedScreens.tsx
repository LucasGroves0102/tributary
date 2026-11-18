/** The user's watched screens, listed where alerts are read and set (Riley, test 10, findings 14 and
 * 72: after watching a screen, the Alerts page still said "choose Watch" and Settings listed none):
 * each by name, linked to the screen, with what it watches and what its last check said. Words the
 * API's watches only (`lastCheck`). Nothing while they load, or from an older API. */

import { href } from "../route";
import type { ScreenWatches } from "../types";
import { lastCheck } from "./watches";

export function WatchedScreens({ list, intro }: { list: ScreenWatches | null | undefined; intro?: string }) {
  if (!list?.watches.length) return null;
  const n = list.watches.length;
  return (
    <div className="watched-screens">
      <p className="small">{intro ?? `You watch ${n === 1 ? "a screen" : `${n} screens`}, checked once a day after the night's figures:`}</p>
      <ul>
        {list.watches.map((w) => {
          const what = [w.entering && "companies entering", w.leaving && "leaving",
            w.lines.length && `${w.lines.length} ${w.lines.length === 1 ? "line" : "lines"} (${w.lines.map((l) => l.label).join(", ")})`]
            .filter(Boolean).join(", ");
          return (
            <li key={w.id}>
              <a href={href({ view: "screen", itemId: w.item_id })}>{w.name}</a>
              {what && <span className="faint">: {what}.</span>}
              <span className="faint small watched-check"> {lastCheck(w, list.max_companies)}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

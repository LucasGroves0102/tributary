import { useCallback, useEffect, useState } from "react";

import { load, save } from "../storage";
import { emptyChart, restoreChart, type ChartState } from "./model";

const key = (user: string) => `tributary.chart.${user}`;

function stored(user: string | null): ChartState {
  if (!user) return emptyChart();
  try {
    return restoreChart(JSON.parse(load(key(user)) ?? "null"));
  } catch {
    return emptyChart();
  }
}

/** The chart builder's state, kept across screens (the assistant and the Chart buttons change
 * it from elsewhere) and remembered per user in this browser. */
export function useChart(user: string | null): [ChartState, (f: (s: ChartState) => ChartState) => void] {
  const [chart, set] = useState<{ user: string | null; state: ChartState }>(() => ({
    user,
    state: stored(user),
  }));
  const current = chart.user === user ? chart.state : stored(user);

  useEffect(() => {
    if (chart.user !== user) set({ user, state: stored(user) });
  }, [user, chart.user]);

  const update = useCallback(
    (f: (s: ChartState) => ChartState) =>
      set((c) => {
        const state = f(c.user === user ? c.state : stored(user));
        if (user) save(key(user), JSON.stringify(state));
        return { user, state };
      }),
    [user],
  );
  return [current, update];
}

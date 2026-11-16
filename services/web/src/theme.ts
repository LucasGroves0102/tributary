import { useCallback, useEffect, useState } from "react";

import { load, remove, save } from "./storage";

export type Theme = "dark" | "light";
/** What the top bar's button says: an explicit theme, or the visitor's system's. */
export type ThemeChoice = Theme | "system";

/** Per user: each demo user keeps their own choice. Before sign-in, the last one used. */
const key = (user: string | null) => (user ? `tributary.theme.${user}` : "tributary.theme");

/** The button cycles through the three: Light, then Dark, then System. */
export const NEXT_THEME: Record<ThemeChoice, ThemeChoice> = { light: "dark", dark: "system", system: "light" };
export const THEME_WORD: Record<ThemeChoice, string> = { light: "Light", dark: "Dark", system: "System" };

/** The theme the visitor chose, or null: then the page follows their system (tokens.css reads
 * prefers-color-scheme itself, so nothing waits for this script). */
export function storedTheme(user: string | null): Theme | null {
  const t = load(key(user)) ?? load(key(null));
  return t === "light" || t === "dark" ? t : null;
}

/** [the choice in force, move to the next one, choose one]. Light and Dark are kept (per user and as the
 * signed-out default); System forgets the choice and the page follows the system again. The top bar offers
 * the three by name (Riley 72: a blind cycle); `cycle` stays for the keyboard. */
export function useTheme(user: string | null): [ThemeChoice, () => void, (choice: ThemeChoice) => void] {
  const [chosen, setChosen] = useState<Theme | null>(() => storedTheme(user));

  useEffect(() => setChosen(storedTheme(user)), [user]);

  useEffect(() => {
    if (chosen) document.documentElement.dataset.theme = chosen;
    else delete document.documentElement.dataset.theme;
  }, [chosen]);

  const choice: ThemeChoice = chosen ?? "system";
  const set = useCallback((next: ThemeChoice) => {
    if (next === "system") {
      remove(key(user));
      remove(key(null));
      setChosen(null);
    } else {
      save(key(user), next);
      save(key(null), next);
      setChosen(next);
    }
  }, [user]);
  const cycle = useCallback(() => set(NEXT_THEME[choice]), [choice, set]);

  return [choice, cycle, set];
}

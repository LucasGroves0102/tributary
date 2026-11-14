import { useCallback, useEffect, useState } from "react";

import { load, remove, save } from "./storage";

export type Theme = "dark" | "light";

/** Per user: each demo user keeps their own choice. Before sign-in, the last one used. */
const key = (user: string | null) => (user ? `tributary.theme.${user}` : "tributary.theme");

const DARK_QUERY = "(prefers-color-scheme: dark)";

/** What the visitor's system asks for (light where it can't say: jsdom, old browsers). */
export function systemTheme(): Theme {
  return typeof window !== "undefined" && window.matchMedia?.(DARK_QUERY).matches ? "dark" : "light";
}

/** The theme the visitor chose, or null: then the page follows their system (tokens.css reads
 * prefers-color-scheme itself, so nothing waits for this script). */
export function storedTheme(user: string | null): Theme | null {
  const t = load(key(user)) ?? load(key(null));
  return t === "light" || t === "dark" ? t : null;
}

/** [the theme on screen, switch to the other one]. Switching to the one the system asks for forgets
 * the choice, so the page follows the system again from there. */
export function useTheme(user: string | null): [Theme, () => void] {
  const [chosen, setChosen] = useState<Theme | null>(() => storedTheme(user));
  const [system, setSystem] = useState<Theme>(systemTheme);

  useEffect(() => setChosen(storedTheme(user)), [user]);

  useEffect(() => {
    const q = window.matchMedia?.(DARK_QUERY);
    if (!q) return;
    const on = () => setSystem(q.matches ? "dark" : "light");
    q.addEventListener?.("change", on);
    return () => q.removeEventListener?.("change", on);
  }, []);

  useEffect(() => {
    if (chosen) document.documentElement.dataset.theme = chosen;
    else delete document.documentElement.dataset.theme;
  }, [chosen]);

  const theme = chosen ?? system;
  const toggle = useCallback(() => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    if (next === systemTheme()) {
      remove(key(user));
      remove(key(null));
      setChosen(null);
    } else {
      save(key(user), next);
      save(key(null), next);
      setChosen(next);
    }
  }, [theme, user]);

  return [theme, toggle];
}

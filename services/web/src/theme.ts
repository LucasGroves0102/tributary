import { useCallback, useEffect, useState } from "react";

import { load, save } from "./storage";

export type Theme = "dark" | "light";

/** Per user: each demo user keeps their own choice. Before sign-in, the last one used. */
const key = (user: string | null) => (user ? `tributary.theme.${user}` : "tributary.theme");

export function storedTheme(user: string | null): Theme {
  const t = load(key(user)) ?? load(key(null));
  return t === "light" ? "light" : "dark";
}

export function useTheme(user: string | null): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => storedTheme(user));

  useEffect(() => setTheme(storedTheme(user)), [user]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const toggle = useCallback(() => {
    setTheme((t) => {
      const next = t === "dark" ? "light" : "dark";
      save(key(user), next);
      save(key(null), next);
      return next;
    });
  }, [user]);

  return [theme, toggle];
}

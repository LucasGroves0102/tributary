/** localStorage for per-browser conveniences (theme, drawer, last visit). It can throw or be
 * empty (private windows, blocked site data), so every access is guarded and nothing here is
 * ever required for the app to work. */

export function load(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function save(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // ignore: the preference just won't survive a reload
  }
}

export function remove(key: string): void {
  try {
    window.localStorage.removeItem(key);
  } catch {
    // ignore
  }
}

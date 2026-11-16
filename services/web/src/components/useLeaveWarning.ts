import { useEffect } from "react";

/** While there are unsaved changes, closing or reloading the tab asks first (Riley 85: leaving a thesis
 * with an unsaved embed said nothing). The browser words the question; a hash change inside the app
 * isn't a leave (a thesis keeps its draft in this browser, a step switch keeps the editor mounted). */
export function useLeaveWarning(dirty: boolean) {
  useEffect(() => {
    if (!dirty) return;
    const ask = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      // older browsers want a value; every browser shows its own words
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", ask);
    return () => window.removeEventListener("beforeunload", ask);
  }, [dirty]);
}

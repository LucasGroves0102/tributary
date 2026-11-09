/** How an analysis project talks to its thesis editor while both are on screen (the project
 * keeps the editor mounted, hidden when another step shows): the editor registers a handle,
 * the project pins evidence through it, so a pin lands in the text being edited instead of
 * racing it, and reports its status (unsaved changes, the saved text) for the step markers
 * and the Monitor step's check button. */

import type { PinTarget } from "./project";

export interface ThesisHandle {
  itemId: string;
  /** appends `block` to a section: saved at once when there were no unsaved changes
   * ("saved"), else left in the editor as an unsaved change ("draft") */
  pin: (target: PinTarget, block: string) => Promise<"saved" | "draft">;
  /** the text being edited (unsaved changes included), for the pin menu's pillars */
  markdown: () => string;
  /** marks a check's quote in the preview, or selects it in the editor */
  showQuote: (quote: string) => void;
}

export interface ThesisStatus {
  itemId: string;
  dirty: boolean;
  /** the Markdown as last saved */
  saved: string;
}

/** A thesis draft kept in the browser until it is saved (per user and thesis): leaving the
 * page or the project doesn't lose it. */
export interface StoredDraft {
  name: string;
  markdown: string;
  companyId: string | null;
  /** the saved version the draft started from (the editor's saved fingerprint) */
  base: string;
  /** ISO time of the last change */
  at: string;
}

export const draftKey = (user: string, itemId: string) => `tributary.thesis-draft.${user}.${itemId}`;

/** A stored draft (untrusted: localStorage) or null. */
export function restoreDraft(raw: string | null): StoredDraft | null {
  if (!raw) return null;
  try {
    const o = JSON.parse(raw) as Record<string, unknown>;
    if (typeof o.name !== "string" || typeof o.markdown !== "string" || typeof o.base !== "string" || typeof o.at !== "string") return null;
    const companyId = typeof o.companyId === "string" && /^\d{10}$/.test(o.companyId) ? o.companyId : null;
    return { name: o.name.slice(0, 120), markdown: o.markdown, companyId, base: o.base, at: o.at };
  } catch {
    return null;
  }
}

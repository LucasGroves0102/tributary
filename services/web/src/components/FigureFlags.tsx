import type { RefObject } from "react";

/** How many figures to name before "+N more" (all are marked in the text either way). */
export const NAMED = 6;

/** The figures an answer or check wrote that aren't in the data, named (#57): each a button that
 * scrolls to where the text marks it (markdown.ts `flagged` spans carry `data-figure`) and
 * flashes it. `within`: the element holding the rendered text. Plain React text, no HTML. */
export function FigureFlags({ figures, within, lead }: {
  figures: string[];
  within: RefObject<HTMLElement | null>;
  /** "2 figures not in the data" */
  lead: string;
}) {
  const unique = [...new Set(figures)];
  if (!unique.length) return null;
  const show = (figure: string) => {
    const marks = [...(within.current?.querySelectorAll<HTMLElement>(".figure.unverified") ?? [])];
    const mark = marks.find((m) => m.dataset.figure === figure) ?? null;
    if (!mark) return;
    mark.scrollIntoView?.({ block: "center", behavior: "smooth" });
    mark.classList.remove("flash");
    void mark.offsetWidth; // restart the animation
    mark.classList.add("flash");
  };
  return (
    <span className="figure-flags">
      <span className="badge warn" title="Marked in the text: not found in anything the assistant read">⚠ {lead}:</span>{" "}
      {unique.slice(0, NAMED).map((f) => (
        <button key={f} type="button" className="figure-flag" onClick={() => show(f)} title={`Show ${f} in the text`}>
          {f}
        </button>
      ))}
      {unique.length > NAMED && <span className="faint small"> +{unique.length - NAMED} more, marked in the text</span>}
    </span>
  );
}

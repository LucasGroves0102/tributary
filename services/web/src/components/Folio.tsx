import { useId, type ReactNode } from "react";

/** A section of a Seal page with its rubric in the margin (seal.css `.folio`): the red name (a
 * heading), an italic line saying what the section covers, and the section beside it. Below 1100px
 * of page the rubric sits above the section. */
export function Folio({ name, line, aside, lead = false, className, children }: {
  name: string;
  /** what the section covers: "Twelve months to Jul 26, 2026" */
  line?: ReactNode;
  /** a quieter second line under it */
  aside?: ReactNode;
  /** the page's first section: no rule above it */
  lead?: boolean;
  className?: string;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <section className={["folio", lead && "lead", className].filter(Boolean).join(" ")} aria-labelledby={id}>
      <header className="rubric">
        <h2 id={id}>{name}</h2>
        {(line || aside) && (
          <p>
            {line}
            {aside && <span className="aside">{aside}</span>}
          </p>
        )}
      </header>
      <div className="folio-body">{children}</div>
    </section>
  );
}

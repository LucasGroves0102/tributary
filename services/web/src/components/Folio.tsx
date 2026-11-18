import { createContext, useContext, useId, type ReactNode } from "react";

import { day } from "../format";

/** As of a past day (`?asof=`), the sections that still show today's data sit inside this with the
 * day: each such Folio says so at its top (Riley, test 10, finding 18). null: nothing to say. */
export const TodaysView = createContext<string | null>(null);

/** A section of a Seal page with its rubric in the margin (seal.css `.folio`): the red name (a
 * heading), an italic line saying what the section covers, and the section beside it. Below 1100px
 * of page the rubric sits above the section. */
export function Folio({ name, line, aside, lead = false, className, id: sectionId, children }: {
  name: string;
  /** what the section covers: "Twelve months to Jul 26, 2026" */
  line?: ReactNode;
  /** a quieter second line under it */
  aside?: ReactNode;
  /** the page's first section: no rule above it */
  lead?: boolean;
  className?: string;
  /** the section's own id, for a page's contents to scroll to (the address is the hash route, so never a #fragment link) */
  id?: string;
  children: ReactNode;
}) {
  const id = useId();
  const asOf = useContext(TodaysView);
  return (
    <section className={["folio", lead && "lead", className].filter(Boolean).join(" ")} aria-labelledby={id} id={sectionId}>
      <header className="rubric">
        <h2 id={id}>{name}</h2>
        {(line || aside) && (
          <p>
            {line}
            {aside && <span className="aside">{aside}</span>}
          </p>
        )}
      </header>
      <div className="folio-body">
        {asOf && <p className="asof-today" role="note">Today's, not as it stood on {day(asOf, true)}.</p>}
        {children}
      </div>
    </section>
  );
}

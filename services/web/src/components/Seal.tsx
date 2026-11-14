import * as Tooltip from "@radix-ui/react-tooltip";
import { useId } from "react";

/** The wax seal: Tributary's mark, and on a figure, "taken from the filing" (.interface-design/
 * system.md). One drawing, defined once in the page (SealSprite, main.tsx) and used by reference, so
 * the many seals of a ledger cost nothing. Its colours are its own (red wax, a gold ring and T): the
 * only place gold appears. */
export function SealSprite() {
  return (
    <svg className="seal-sprite" width="0" height="0" aria-hidden="true" focusable="false">
      <defs>
        <radialGradient id="seal-wax-fill" cx="40%" cy="35%" r="70%">
          <stop offset="0" stopColor="#d23a46" />
          <stop offset="0.6" stopColor="#a8182a" />
          <stop offset="1" stopColor="#6e0d19" />
        </radialGradient>
        <symbol id="seal-wax" viewBox="0 0 40 40">
          <path
            d="M20 2.5c2.2 0 3 1.6 4.8 2.1 1.9.5 3.5-.4 5.2.6 1.6 1 1.6 2.9 2.8 4.3 1.3 1.4 3.1 1.8 3.8 3.6.6 1.8-.3 3.4-.1 5.3.2 1.9 1.4 3.3 1 5.2-.4 1.9-2.1 2.6-3 4.2-.9 1.7-.8 3.6-2.3 4.9-1.5 1.2-3.3.8-5.1 1.6-1.8.7-2.8 2.4-4.8 2.6-2 .2-3.2-1.3-5.1-1.8-1.9-.5-3.6.2-5.2-.9-1.6-1.1-1.5-3-2.6-4.5-1.2-1.5-3-2-3.6-3.8-.6-1.8.4-3.4.3-5.3-.1-1.9-1.2-3.4-.7-5.3.5-1.8 2.3-2.4 3.3-4 1-1.6 1-3.5 2.6-4.7 1.6-1.1 3.4-.6 5.2-1.3C17 4 17.9 2.5 20 2.5Z"
            fill="url(#seal-wax-fill)"
          />
          <circle cx="20" cy="20.5" r="10.5" fill="none" stroke="#e2c27a" strokeOpacity="0.6" strokeWidth="1" />
          <path d="M14.2 15.2h11.6M20 15.2v11.2M16.4 26.4h7.2" stroke="#f1d892" strokeOpacity="0.95" strokeWidth="1.8" strokeLinecap="round" />
        </symbol>
      </defs>
    </svg>
  );
}

/** The seal alone: the logo (30px), after a sentence (20px), in a ledger (14px). Decorative unless
 * labelled. */
export function Seal({ size = "ledger", label }: { size?: "logo" | "lead" | "ledger"; label?: string }) {
  return (
    <svg className={`seal seal-${size}`} viewBox="0 0 40 40" {...(label ? { role: "img", "aria-label": label } : { "aria-hidden": true })}>
      <use href="#seal-wax" />
    </svg>
  );
}

/** A seal on a figure taken from a filing: it opens the filing where the figure is printed, and its
 * hover (or focus) says which filing and how the figure was read. It replaces the hover-only source:
 * the figure's own text stays the figure. */
export function SealLink({ href, label, source, size = "ledger" }: {
  /** the reader at the filing's statements (or EDGAR) */
  href: string;
  /** what a screen reader hears: "Revenue: taken from the 10-Q filed Aug 26, 2026" */
  label: string;
  /** the full source line, shown on hover and focus */
  source: string;
  size?: "lead" | "ledger";
}) {
  const external = href.startsWith("https://");
  const described = useId();
  return (
    <Tooltip.Provider delayDuration={250} skipDelayDuration={150}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>
          <a className={`seal-link seal-link-${size}`} href={href} aria-label={label} aria-describedby={described}
            {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}>
            <Seal size={size} />
          </a>
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content className="tip" side="top" align="end" sideOffset={6} collisionPadding={12} aria-hidden="true">
            {source}
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
      {/* the whole source for a screen reader, without waiting for a hover */}
      <span id={described} className="seal-source" hidden>{source}</span>
    </Tooltip.Provider>
  );
}

import * as Tooltip from "@radix-ui/react-tooltip";
import { useId } from "react";

/** Tributary's two drawings, defined once in the page (SealSprite, main.tsx) and used by reference: the
 * logo (a red disk with its ring and T; the disk stays #B3111E in both themes) and the auditor's tick,
 * ✓ in seal red, which says a figure agrees to the filing (.interface-design/system.md §5). */
export function SealSprite() {
  return (
    <svg className="seal-sprite" width="0" height="0" aria-hidden="true" focusable="false">
      <defs>
        <symbol id="logo-disk" viewBox="0 0 22 22">
          <circle cx="11" cy="11" r="10" fill="#b3111e" />
          <circle cx="11" cy="11" r="7.2" fill="none" stroke="#ffffff" strokeOpacity="0.75" strokeWidth="1.2" />
          <path d="M7.4 7.6h7.2M11 7.6v7.4" stroke="#ffffff" strokeWidth="1.8" strokeLinecap="round" />
        </symbol>
        <symbol id="tick" viewBox="0 0 12 12">
          <path d="M2 6.4 4.8 9 10 3" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
        </symbol>
      </defs>
    </svg>
  );
}

/** The logo (22px), or on a figure the tick (14px in a ledger, 22px on a hero): "agrees to the filing".
 * Decorative unless labelled. */
export function Seal({ size = "ledger", label }: { size?: "logo" | "lead" | "ledger"; label?: string }) {
  const a11y = label ? { role: "img", "aria-label": label } : { "aria-hidden": true as const };
  return size === "logo" ? (
    <svg className="seal seal-logo" viewBox="0 0 22 22" {...a11y}>
      <use href="#logo-disk" />
    </svg>
  ) : (
    <svg className={`seal seal-tick seal-${size}`} viewBox="0 0 12 12" {...a11y}>
      <use href="#tick" />
    </svg>
  );
}

/** A tick on a figure taken from a filing, on pages not yet on source cards: it opens the filing where
 * the figure is printed, and its hover (or focus) says which filing and how the figure was read. */
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
    <Tooltip.Provider delayDuration={200} skipDelayDuration={400}>
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

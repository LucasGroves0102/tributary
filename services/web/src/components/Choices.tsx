import * as ToggleGroup from "@radix-ui/react-toggle-group";
import { useLayoutEffect, useRef } from "react";

/** One of a few views (Quarterly / Annual / TTM): the spec's segmented control (§2.5), a `well` track
 * with a `sheet` thumb and a `rule-2` ring, 2 to 4 options. A Radix toggle group, so arrow keys move
 * between the options and a screen reader hears a group of radio items with the chosen one checked. It
 * always has a value: choosing the chosen option again keeps it. The thumb slides 180ms (`--t-tab`); it
 * jumps under reduced motion and on the first draw. */
export function Choices<T extends string>({ value, options, set, label, off, className }: {
  value: T;
  options: [T, string][];
  set: (v: T) => void;
  label: string;
  /** an option that doesn't apply here, and why (its hover) */
  off?: Partial<Record<T, string>>;
  className?: string;
}) {
  const root = useRef<HTMLDivElement>(null);
  const thumb = useRef<HTMLSpanElement>(null);
  const drawn = useRef(false);
  useLayoutEffect(() => {
    const on = root.current?.querySelector<HTMLElement>('[data-state="on"]');
    const t = thumb.current;
    if (!t) return;
    if (!on || !on.offsetWidth) {
      t.hidden = !on;
      return;
    }
    t.hidden = false;
    if (!drawn.current) t.classList.add("no-anim");
    t.style.width = `${on.offsetWidth}px`;
    t.style.transform = `translateX(${on.offsetLeft}px)`;
    if (!drawn.current) {
      void t.offsetWidth;
      t.classList.remove("no-anim");
      drawn.current = true;
    }
  });
  return (
    <ToggleGroup.Root ref={root} type="single" className={className ? `choices ${className}` : "choices"} aria-label={label} value={value}
      onValueChange={(v) => v && v !== value && set(v as T)}>
      <span ref={thumb} className="choices-thumb" aria-hidden="true" />
      {options.map(([v, l]) => (
        <ToggleGroup.Item key={v} value={v} disabled={!!off?.[v]} title={off?.[v]}>
          {l}
        </ToggleGroup.Item>
      ))}
    </ToggleGroup.Root>
  );
}

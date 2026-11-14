import * as ToggleGroup from "@radix-ui/react-toggle-group";

/** One of a few views (Quarterly / Annual / TTM): a Radix toggle group, so arrow keys move between
 * the options and a screen reader hears a group of radio items with the chosen one checked. It
 * always has a value: choosing the chosen option again keeps it. Styled `.choices` (seal.css): an
 * ink frame, the chosen option inverted. */
export function Choices<T extends string>({ value, options, set, label, off, className }: {
  value: T;
  options: [T, string][];
  set: (v: T) => void;
  label: string;
  /** an option that doesn't apply here, and why (its hover) */
  off?: Partial<Record<T, string>>;
  className?: string;
}) {
  return (
    <ToggleGroup.Root type="single" className={className ? `choices ${className}` : "choices"} aria-label={label} value={value}
      onValueChange={(v) => v && v !== value && set(v as T)}>
      {options.map(([v, l]) => (
        <ToggleGroup.Item key={v} value={v} disabled={!!off?.[v]} title={off?.[v]}>
          {l}
        </ToggleGroup.Item>
      ))}
    </ToggleGroup.Root>
  );
}

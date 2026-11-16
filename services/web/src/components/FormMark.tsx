/** A filing's form, marked the same way everywhere (Riley 109: 10-K red, 10-Q black, 8-K grey, with no
 * legend). An annual report is filled ink, a quarterly report outlined, anything else a step quieter,
 * an expected filing dashed; the hover names the form. A red-letter day colours its own 8-K (seal.css
 * `.event.red`), nothing else here is red. */

const NAMES: [RegExp, string][] = [
  [/^10-K/, "annual report"],
  [/^10-Q/, "quarterly report"],
  [/^8-K/, "current report: an event the company must disclose"],
  [/^20-F/, "a foreign company's annual report"],
  [/^40-F/, "a Canadian company's annual report"],
  [/^6-K/, "a foreign company's current report"],
  [/^(3|4|5)(\/A)?$/, "insider ownership report"],
  [/^DEF 14A/, "proxy statement"],
  [/^S-1/, "registration statement"],
  [/^SC 13[DG]/, "a large holder's ownership report"],
];

/** "annual report" for 10-K and 10-K/A; "" for a form not listed. */
export function formName(form: string): string {
  const found = NAMES.find(([re]) => re.test(form));
  if (!found) return "";
  return form.endsWith("/A") ? `${found[1]}, amended` : found[1];
}

/** k (annual), q (quarterly) or "" (anything else). */
export function formKind(form: string): "k" | "q" | "" {
  return form.startsWith("10-K") ? "k" : form.startsWith("10-Q") ? "q" : "";
}

export function FormMark({ form, expected = false, title }: { form: string; expected?: boolean; title?: string | null }) {
  const name = formName(form);
  // null: the row around it has its own hover
  const hover = title === null ? undefined : title ?? (expected ? `${form} expected${name ? ` (${name})` : ""}: an estimate, not filed yet` : name ? `${form}: ${name}` : undefined);
  return <span className={`tag ${expected ? "e" : formKind(form)}`} title={hover}>{form}</span>;
}

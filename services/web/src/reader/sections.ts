/** Section keys in words. The keys ("I-2", "1A", "EX-99.1") are how the API and the parser name
 * sections; a reader knows them as MD&A, Risk Factors, a press release. Works from the key alone
 * (the key's shape says which form it belongs to), for places that hold only a key: Home's
 * Continue list, the assistant's source links. The reader's outline has each section's own title. */

/** 8-K items (Form 8-K General Instructions B), short labels. */
export const EIGHT_K_ITEMS: Record<string, string> = {
  "1.01": "Material agreement",
  "1.02": "Agreement terminated",
  "1.03": "Bankruptcy",
  "1.05": "Cybersecurity incident",
  "2.01": "Acquisition or disposition",
  "2.02": "Results of operations",
  "2.03": "New debt obligation",
  "2.04": "Debt acceleration",
  "2.05": "Exit or restructuring costs",
  "2.06": "Impairment",
  "3.01": "Delisting notice",
  "3.02": "Unregistered equity sale",
  "3.03": "Change to holders' rights",
  "4.01": "Auditor change",
  "4.02": "Non-reliance on prior financials",
  "5.01": "Change in control",
  "5.02": "Officer or director change",
  "5.03": "Bylaws or fiscal year change",
  "5.05": "Code of ethics",
  "5.07": "Shareholder vote",
  "7.01": "Regulation FD disclosure",
  "8.01": "Other events",
  "9.01": "Financial statements and exhibits",
};

const TEN_K: Record<string, string> = {
  "1": "Business",
  "1A": "Risk Factors",
  "1B": "Unresolved Staff Comments",
  "1C": "Cybersecurity",
  "2": "Properties",
  "3": "Legal Proceedings",
  "4": "Mine Safety Disclosures",
  "5": "Market for the Stock",
  "7": "MD&A",
  "7A": "Market Risk",
  "8": "Financial Statements",
  "9": "Changes in Accountants",
  "9A": "Controls and Procedures",
  "9B": "Other Information",
  "10": "Directors and Governance",
  "11": "Executive Compensation",
  "12": "Security Ownership",
  "13": "Related Transactions",
  "14": "Accountant Fees",
  "15": "Exhibits",
};

const TEN_Q: Record<string, string> = {
  "I-1": "Financial Statements",
  "I-2": "MD&A",
  "I-3": "Market Risk",
  "I-4": "Controls and Procedures",
  "II-1": "Legal Proceedings",
  "II-1A": "Risk Factors",
  "II-2": "Share Sales and Buybacks",
  "II-3": "Defaults",
  "II-4": "Mine Safety Disclosures",
  "II-5": "Other Information",
  "II-6": "Exhibits",
};

/** A section's own title, or its name from the key when the parser left none (rule 10 empties a
 * title that was only punctuation, such as an 8-K's ","). */
export function sectionTitle(key: string, title: string | null | undefined): string {
  return title?.trim() || sectionName(key);
}

/** "MD&A", "Risk Factors", "Press release", "Exhibit 10.1", "Item 3C" for one it doesn't know. */
export function sectionName(key: string): string {
  const known = TEN_Q[key] ?? TEN_K[key] ?? EIGHT_K_ITEMS[key];
  if (known) return known;
  const exhibit = /^EX-(\d+(?:\.\d+)?)/i.exec(key);
  if (exhibit) return /^99(\.|$)/.test(exhibit[1]!) ? "Press release" : `Exhibit ${exhibit[1]}`;
  return `Item ${key}`;
}

import { useState } from "react";

import { Folio } from "../components/Folio";

/** Where the add-ins are served (public/addins; nginx `location /addins/`). */
export const EXCEL_MANIFEST = "/addins/excel/manifest.xml";
export const SHEETS_SCRIPT = "/addins/sheets/Tributary.gs";
const SIDELOAD = "https://learn.microsoft.com/office/dev/add-ins/testing/test-debug-office-add-ins#sideload-an-office-add-in-for-testing";

type Script = { kind: "closed" } | { kind: "loading" } | { kind: "ok"; text: string } | { kind: "error" };

async function fetchScript(): Promise<string> {
  const res = await fetch(SHEETS_SCRIPT, { credentials: "omit" });
  if (!res.ok) throw new Error(String(res.status));
  return res.text();
}

/** "In your spreadsheet": the Excel add-in and the Google Sheets script, both over the batch route
 * (POST /v1/sheets/values) with the reader's own API key. The script is read only when asked for. */
export function InYourSpreadsheet({ origin = window.location.origin, loadScript = fetchScript }: {
  origin?: string;
  loadScript?: () => Promise<string>;
}) {
  const [script, setScript] = useState<Script>({ kind: "closed" });
  const [copied, setCopied] = useState<"yes" | "no" | null>(null);

  const open = (isOpen: boolean) => {
    if (!isOpen || script.kind === "ok" || script.kind === "loading") return;
    setScript({ kind: "loading" });
    loadScript().then((text) => setScript({ kind: "ok", text }), () => setScript({ kind: "error" }));
  };
  const copy = () => {
    if (script.kind !== "ok") return;
    const write = navigator.clipboard?.writeText(script.text);
    if (!write) return setCopied("no");
    write.then(() => setCopied("yes"), () => setCopied("no"));
  };

  return (
    <Folio name="In your spreadsheet" line="Excel and Google Sheets, with the same key">
      <p>
        Formulas that fetch Tributary's figures into cells, each from the filing it came from: the same figures, readings and
        permission checks as this site, with your API key (read-only is enough).
      </p>
      <dl className="api-scopes api-formulas">
        <dt><code>=TRIBUTARY.VALUE("NVDA", "revenue")</code></dt>
        <dd>Excel: the newest quarter's revenue. In Google Sheets, <code>=TRIBUTARY("NVDA", "revenue")</code>.</dd>
        <dt><code>=TRIBUTARY.VALUE("NVDA", "net_income", "FY2024")</code></dt>
        <dd>A fiscal year. A range of companies works too: <code>=TRIBUTARY(A2:A30, "net_income", "FY2024")</code> is one request.</dd>
        <dt><code>=TRIBUTARY.VALUE("ASML", "revenue", "TTM", , "EUR")</code></dt>
        <dd>A figure in its own currency: without the currency, an amount not in US dollars says so instead of a number.</dd>
        <dt><code>=TRIBUTARY.SERIES("NVDA", "gross_margin", 8, "TTM")</code></dt>
        <dd>The newest eight periods, oldest first, spilled: period and figure on each row (<code>TRIBUTARY_SERIES</code> in Google
          Sheets).</dd>
        <dt><code>=HYPERLINK(TRIBUTARY.SOURCE("NVDA", "revenue"))</code></dt>
        <dd>The filing the figure came from, on EDGAR (<code>TRIBUTARY_SOURCE</code> in Google Sheets).</dd>
      </dl>
      <p>
        Periods: <code>latest</code> (the default), <code>TTM</code>, <code>FY</code> (the newest fiscal year), <code>FY2024</code>,{" "}
        <code>FY2024 Q3</code>, <code>CY2024 Q3</code>, a quarter's end day (<code>2024-09-29</code>), or <code>TTM</code> before a quarter.
        Then an as-of day (the figure as it stood then) and the currency you accept. EPS is split-adjusted, as on Statements.
      </p>

      <h3 className="api-sub">Excel</h3>
      <ol className="api-steps">
        <li>
          Download the add-in's manifest, <a className="api-manifest" href={`${origin}${EXCEL_MANIFEST}`} download="tributary-excel.xml">tributary-excel.xml</a>.
        </li>
        <li>
          In Excel on the web: Home, Add-ins, More add-ins, My add-ins, Upload my add-in, and choose the file. Excel for Windows and
          Mac load it the way Microsoft describes for{" "}
          <a href={SIDELOAD} target="_blank" rel="noreferrer">sideloading an add-in</a>.
        </li>
        <li>
          Open Tributary on the Home tab and paste your key. It stays in the add-in's storage on that computer and goes only to
          Tributary.
        </li>
      </ol>

      <h3 className="api-sub">Google Sheets</h3>
      <ol className="api-steps">
        <li>In your spreadsheet: Extensions, Apps Script.</li>
        <li>
          Replace everything in <code>Code.gs</code> with the script (<a className="api-script" href={`${origin}${SHEETS_SCRIPT}`}
            download="Tributary.gs">download it</a>, or copy it below), then Save.
        </li>
        <li>
          Reload the spreadsheet: a Tributary menu appears. Tributary, Set API key, and paste your key. Google asks you once to let
          the script reach an outside service and show a prompt.
        </li>
      </ol>
      <details className="api-script-code" onToggle={(e) => open((e.currentTarget as HTMLDetailsElement).open)}>
        <summary>The script</summary>
        {script.kind === "loading" && <p className="muted">Loading the script…</p>}
        {script.kind === "error" && <p className="error" role="alert">Couldn't load the script: download it instead.</p>}
        {script.kind === "ok" && (
          <>
            <p className="row wrap">
              <button className="btn sm" onClick={copy}>{copied === "yes" ? "Copied" : "Copy the script"}</button>
              {copied === "no" && <span className="small muted">This browser didn't let the page copy it: select it and copy it yourself.</span>}
            </p>
            <pre className="api-code" tabIndex={0} aria-label="The Google Sheets script"><code>{script.text}</code></pre>
          </>
        )}
      </details>
      <p>
        Google runs a spreadsheet's formulas with its owner's key: anyone who can edit the spreadsheet can edit the script, and so use
        that key. Share a copy without the script, or view-only.
      </p>

      <h3 className="api-sub">Limits and citations</h3>
      <p>
        Cells are sent together, up to 200 figures and 25 companies a request, and each request counts once against the key's limits.
        Excel keeps answers ten minutes, Google Sheets six hours; the add-in's Refresh figures and the menu's Refresh figures ask again.
        In Google Sheets every formula is its own request, so give a range one formula rather than one per cell.
      </p>
      <p>
        Every figure comes with its filing: <code>SOURCE</code> gives its EDGAR link, or its <code>period</code>, <code>filed</code>,{" "}
        <code>form</code>, <code>accession</code>, <code>unit</code>, <code>status</code> or <code>reason</code>. Where Tributary has no
        number to show (stale, n/m, n/a, old, another currency, nothing on file, or a company you can't read), the cell is an error with
        the reason (in a Google Sheets range, the reason as text). Tributary never converts currencies: give the currency to read a
        figure in it. A stale or old latest figure can still be read by asking for its period.
      </p>
    </Folio>
  );
}

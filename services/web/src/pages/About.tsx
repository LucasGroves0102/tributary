import { href } from "../route";

/** What the product covers and what it deliberately doesn't, in one place, for readers who'd
 * otherwise look for a P/E or a consensus beat. Static copy: keep it in step with the agent's
 * system prompt (prices and consensus refused) and the Query API's routes. */
export const NOT_COVERED: { title: string; text: string }[] = [
  {
    title: "No stock prices or market data",
    text: "So no market cap, P/E, EV/EBITDA or other valuation multiples, and no share-price returns. Ratios here are from the financial statements alone.",
  },
  {
    title: "No consensus estimates",
    text: "No beat or miss versus analysts. Recaps compare a quarter with the prior quarter and the same quarter a year ago.",
  },
  {
    title: "No earnings-call transcripts or slides",
    text: "Management commentary comes only from filings: MD&A, risk factors and the press releases filed with 8-Ks.",
  },
];

export const GOOD_TO_KNOW: { title: string; text: string }[] = [
  {
    title: "Expected filing dates are estimates",
    text: "From each company's own filing history, capped at the SEC deadline, not dates the company announced.",
  },
  {
    title: "Banks: some metrics are n/a",
    text: "Operating margin, free cash flow and net debt show \"n/a (bank)\": deposits are a bank's funding and lending its operations, so those measures don't describe it.",
  },
  {
    title: "After-hours filings arrive the next morning",
    text: "EDGAR accepts filings until 10 pm ET, but most accepted after 5:30 pm ET reach its feed the next business morning. The SEC's XBRL data can lag a filing by a day or two; each company's Data notes say when.",
  },
  {
    title: "A report counts as 3 assistant questions",
    text: "\"Write a report\" on a workspace has the assistant read every saved chart, comps table and screen (up to 8) and write a cited note, so it uses 3 of your hourly questions, or of a demo sandbox's daily ones. Figures it can't trace to the data are flagged in the report.",
  },
  {
    title: "Derived and restated figures",
    text: "When a filing reports only the year, fiscal Q4 is the year minus the first nine months, and marked derived. Restated figures show the latest filing's value; Data notes list what changed.",
  },
];

export function About() {
  return (
    <div className="page about">
      <div className="row">
        <h1 className="grow">What Tributary covers</h1>
      </div>
      <div className="cover-grid">
        <section className="card">
          <div className="card-h">
            <h2>Included</h2>
          </div>
          <ul className="cover-list">
            <li>
              <b>SEC filings</b>
              <span>10-K, 10-Q and 8-K, parsed into sections (with 8-K press releases), plus the full SEC filing index for every company.</span>
            </li>
            <li>
              <b>XBRL financial data</b>
              <span>Statements, segments and ratios from the figures companies file with the SEC: quarterly, TTM, fiscal or calendar, 10+ years.</span>
            </li>
            <li>
              <b>A source for every number</b>
              <span>Each value links to the filing it came from, with its unit and period; the assistant answers only from the same data and cites it.</span>
            </li>
            <li>
              <b>Only what your plan grants</b>
              <span>Values need financials access to a company, filing text the pro plan; watching a company grants nothing.</span>
            </li>
          </ul>
        </section>
        <section className="card">
          <div className="card-h">
            <h2>Not included</h2>
          </div>
          <ul className="cover-list">
            {NOT_COVERED.map((x) => (
              <li key={x.title}>
                <b>{x.title}</b>
                <span>{x.text}</span>
              </li>
            ))}
          </ul>
        </section>
        <section className="card">
          <div className="card-h">
            <h2>Good to know</h2>
          </div>
          <ul className="cover-list">
            {GOOD_TO_KNOW.map((x) => (
              <li key={x.title}>
                <b>{x.title}</b>
                <span>{x.text}</span>
              </li>
            ))}
          </ul>
        </section>
      </div>
      <p className="faint small">
        Public SEC data only. Start from <a href={href({ view: "companies" })}>Companies</a>, or press{" "}
        <span className="kbd">?</span> for keyboard shortcuts.
      </p>
    </div>
  );
}

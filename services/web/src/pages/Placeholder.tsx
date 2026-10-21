import { href } from "../route";

const COPY = {
  chart: {
    title: "Chart builder",
    coming:
      "Layer any metric or formula across the companies you can read: line or bar, TTM, YoY " +
      "or indexed, fiscal or calendar quarters, with the source filing behind every point and " +
      "PNG, SVG and CSV export.",
  },
  compare: {
    title: "Compare",
    coming:
      "Put a company next to its suggested peers (same SIC, similar size) with revenue, " +
      "growth, margins, free cash flow and leverage side by side, each value linked to its " +
      "filing, and export the table as CSV.",
  },
} as const;

/** Screens that come in a later slice: what they will do, and what works today. */
export function Placeholder({ view }: { view: keyof typeof COPY }) {
  const { title, coming } = COPY[view];
  return (
    <div className="page">
      <div className="row">
        <h1 className="grow">{title}</h1>
      </div>
      <section className="card">
        <div className="card-h">
          <h2>Coming next</h2>
        </div>
        <div className="card-b placeholder">
          <p>{coming}</p>
          <p className="muted">
            Today: each <a href={href({ view: "companies" })}>company page</a> charts 12 quarters
            of its financials with every value linked to its filing, and the assistant (
            <span className="kbd">Ctrl J</span>) answers comparisons with citations.
          </p>
        </div>
      </section>
    </div>
  );
}

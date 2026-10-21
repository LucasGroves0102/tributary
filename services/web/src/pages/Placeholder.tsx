import { href } from "../route";

const COPY = {
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
            Today: the <a href={href({ view: "companies" })}>screener</a> lines companies up on
            growth and margins, the <a href={href({ view: "chart" })}>chart builder</a> puts
            several on one chart, and the assistant (
            <span className="kbd">Ctrl J</span>) answers comparisons with citations.
          </p>
        </div>
      </section>
    </div>
  );
}

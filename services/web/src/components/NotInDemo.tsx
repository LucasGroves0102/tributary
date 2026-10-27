import { screener } from "../api";
import { useLoad } from "../company/useLoad";
import { href } from "../route";
import { demoCount, type Scope } from "../scope";
import { Skeleton } from "./States";

/** A demo sandbox opened a company it can't read (an old or shared link): one friendly page
 * instead of every tab's "not included in your access". The name is public metadata. */
export function NotInDemo({ token, companyId, scope }: { token: string; companyId: string; scope: Scope }) {
  const meta = useLoad(companyId, () =>
    screener(token, { company_id: [companyId], columns: [], sort: "name", limit: 1 }).then(
      (r) => r.rows.find((x) => x.company_id === companyId)?.name ?? null,
    ),
  );
  if (!meta || meta.state === "loading") {
    return <div className="page"><Skeleton lines={2} label="Loading the company" /></div>;
  }
  const name = (meta.state === "ok" && meta.data) || `Company ${companyId}`;
  return (
    <div className="page">
      <section className="card card-b not-in-demo" role="status">
        <h1>{name} isn't one of the {demoCount(scope)} in this demo.</h1>
        <p className="muted">
          The demo sandbox can read {demoCount(scope)}, with their financials and filings.{" "}
          <a href={href({ view: "companies" })}>See them on the Companies page</a>.
        </p>
      </section>
    </div>
  );
}

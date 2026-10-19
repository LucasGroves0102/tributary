import { useEffect, useState } from "react";

export type Route =
  | { view: "ask" }
  | { view: "explore" }
  | { view: "company"; companyId: string }
  | { view: "filing"; companyId: string; accessionNo: string; section: string | null };

const CIK = /^\d{10}$/;
const ACCESSION = /^\d{10}-\d{2}-\d{6}$/;

/** "#/company/0001045810" → route; anything unrecognized is the chat. */
export function parse(hash: string): Route {
  const [view, a, b, c] = hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  if (view === "explore") return { view: "explore" };
  if (view === "company" && CIK.test(a ?? "")) return { view: "company", companyId: a! };
  if (view === "filing" && CIK.test(a ?? "") && ACCESSION.test(b ?? "")) {
    return { view: "filing", companyId: a!, accessionNo: b!, section: c || null };
  }
  return { view: "ask" };
}

export function href(route: Route): string {
  switch (route.view) {
    case "ask":
      return "#/";
    case "explore":
      return "#/explore";
    case "company":
      return `#/company/${route.companyId}`;
    case "filing":
      return `#/filing/${route.companyId}/${route.accessionNo}` +
        (route.section ? `/${encodeURIComponent(route.section)}` : "");
  }
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parse(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parse(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

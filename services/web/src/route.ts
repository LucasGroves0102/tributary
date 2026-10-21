import { useEffect, useState } from "react";

export type Route =
  | { view: "home" }
  | { view: "companies" }
  | { view: "company"; companyId: string }
  | { view: "filing"; companyId: string; accessionNo: string; section: string | null }
  | { view: "chart" }
  | { view: "compare" }
  | { view: "ask" };

/** Screens in the left nav: routes without parameters. */
export type NavView = "home" | "companies" | "chart" | "compare" | "ask";

/** The left nav: label and the key after "g" that opens it. */
export const NAV: { view: NavView; label: string; key: string }[] = [
  { view: "home", label: "Home", key: "h" },
  { view: "companies", label: "Companies", key: "c" },
  { view: "chart", label: "Chart builder", key: "b" },
  { view: "compare", label: "Compare", key: "x" },
  { view: "ask", label: "Ask", key: "a" },
];

const CIK = /^\d{10}$/;
const ACCESSION = /^\d{10}-\d{2}-\d{6}$/;
const SIMPLE = new Set(["companies", "chart", "compare", "ask"]);

/** "#/company/0001045810" → route; anything unrecognized is Home. */
export function parse(hash: string): Route {
  const [view, a, b, c] = hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  if (view && SIMPLE.has(view)) return { view: view as NavView };
  if (view === "explore") return { view: "companies" }; // links from before the v3 shell
  if (view === "company" && CIK.test(a ?? "")) return { view: "company", companyId: a! };
  if (view === "filing" && CIK.test(a ?? "") && ACCESSION.test(b ?? "")) {
    return { view: "filing", companyId: a!, accessionNo: b!, section: c || null };
  }
  return { view: "home" };
}

export function href(route: Route): string {
  switch (route.view) {
    case "home":
      return "#/";
    case "company":
      return `#/company/${route.companyId}`;
    case "filing":
      return `#/filing/${route.companyId}/${route.accessionNo}` +
        (route.section ? `/${encodeURIComponent(route.section)}` : "");
    default:
      return `#/${route.view}`;
  }
}

export function go(route: Route): void {
  window.location.hash = href(route);
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

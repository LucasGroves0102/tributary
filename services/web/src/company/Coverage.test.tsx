import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it } from "vitest";

import type { CoverageGap } from "../types";
import { Coverage } from "./DataNotes";
import { shownGaps } from "./coverage";

const gaps: CoverageGap[] = [
  { kind: "re_registration", cause: "unlinked", reason: "fixable", since: "2026-11-17",
    detail: { moved_on: "2021-04-20", form: "8-K12B", figures_from: "2020-05-02" } },
  { kind: "history_short", cause: "re_registration", reason: "fixable", since: "2026-11-17",
    detail: { figures_from: "2020-05-02", first_xbrl_report: "2009-06-04" } },
  { kind: "documents_missing", cause: "partial", reason: "fixable", since: "2026-11-17",
    detail: { expected: 40, stored: 12 } },
];

it("076: the Coverage group says what is missing and whose gap it is, in words", async () => {
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  await act(async () => root.render(<Coverage gaps={shownGaps(gaps)} name="Marvell" token="t" />));
  const items = [...container.querySelectorAll(".coverage-gap")].map((li) => li.textContent ?? "");
  expect(items).toHaveLength(2); // the short history is the re-registration's, said once
  expect(items[0]).toContain("Figures start at its 2021 re-registration");
  expect(items[0]).toContain("Tributary's gap: on the list to fix");
  expect(items[1]).toContain("12 of Marvell's 40 10-K and 10-Q documents");
  expect(container.querySelector("h3")?.textContent).toBe("Coverage");
  act(() => root.unmount());
  container.remove();
});

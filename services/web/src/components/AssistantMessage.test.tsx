import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it } from "vitest";

import { agentError, ApiError, askFailure } from "../api";
import type { Answer } from "../types";
import { AssistantMessage, type AssistantTurn } from "./AssistantMessage";

let root: Root;
let container: HTMLDivElement;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const ACC = "0001045810-26-000075";

function answer(text: string, figures: [string, string][]): Answer {
  return {
    text,
    stop: "end_turn",
    model: "claude-haiku-5-5",
    rounds: 2,
    tool_calls: [],
    usage: { input_tokens: 10, output_tokens: 5, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 },
    cost_usd: 0.001,
    citations: [{ accession_no: ACC, form: "10-Q", filed: "2026-08-26", company_id: "0001045810", url: null, sections: [] }],
    verification: {
      figures: figures.map(([t, status]) => ({ text: t, value: 0, status, source: null })),
      unverified_accessions: [],
      used_data: true,
      freshness: true,
      ok: figures.every(([, s]) => s !== "unverified"),
    },
    actions: [],
  } as unknown as Answer;
}

const turn = (a: Answer): AssistantTurn => ({ id: 1, role: "assistant", rounds: {}, tools: [], answer: a, actions: [] });

it("names the figures not in the data, marks them in the answer, and each name finds its mark (#57)", () => {
  const a = answer(`| Revenue | $96.22B | vs $46.74B |\n|---|---|---|\n\nNet income vs $26.42B (${ACC}). Freshness: as of 2026-11-06.`,
    [["$96.22B", "verified"], ["$46.74B", "unverified"], ["$26.42B", "unverified"]]);
  act(() => root.render(<AssistantMessage turn={turn(a)} />));
  const flags = container.querySelector(".figure-flags")!;
  expect(flags.textContent).toBe("⚠ 2 figures not in the data: $46.74B$26.42B");
  const marks = [...container.querySelectorAll<HTMLElement>(".answer .figure.unverified")];
  expect(marks.map((m) => m.dataset.figure)).toEqual(["$46.74B", "$26.42B"]);
  let scrolled: Element | null = null;
  Element.prototype.scrollIntoView = function (this: Element) {
    scrolled = this;
  };
  const second = [...flags.querySelectorAll("button")].find((b) => b.textContent === "$26.42B")!;
  act(() => second.click());
  expect(scrolled).toBe(marks[1]);
  expect(marks[1]!.classList.contains("flash")).toBe(true);
});

it("a question that got no answer says so, and that it didn't count", () => {
  const t: AssistantTurn = { id: 1, role: "assistant", rounds: {}, tools: [], actions: [], error: agentError("model unavailable"), refunded: true };
  act(() => root.render(<AssistantMessage turn={t} />));
  expect(container.querySelector(".error")?.textContent).toBe(
    "the model didn't answer; try again in a moment It didn't count against your questions.");
});

it("a refused question says what the user can change; only a 5xx or no answer says try again soon", () => {
  expect(askFailure(new ApiError("Unprocessable Entity", 422))).toBe("That question couldn't be sent: it's empty or longer than 2,000 characters.");
  expect(askFailure(new ApiError("You've reached the limit of 30 questions per hour. Try again later.", 429))).toBe(
    "You've reached the limit of 30 questions per hour. Try again later.");
  expect(askFailure(new ApiError("query api unavailable", 503))).toBe("The assistant isn't answering right now. Try again in a moment.");
  expect(askFailure(new TypeError("Failed to fetch"))).toBe("The assistant is unreachable. Try again in a moment.");
});

it("says every figure verified when they all are, naming none", () => {
  act(() => root.render(<AssistantMessage turn={turn(answer(`Revenue was $96.22B (${ACC}). As of 2026-11-06.`, [["$96.22B", "verified"]]))} />));
  expect(container.querySelector(".figure-flags")).toBeNull();
  expect(container.querySelector(".badge.ok")?.textContent).toBe("✓ 1 figure verified against the data");
});

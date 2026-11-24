import { act, createRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { AssistantPanel } from "./AssistantPanel";
import type { Assistant } from "./useAssistant";

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
  vi.unstubAllGlobals();
});

const assistant: Assistant = {
  turns: [{ id: 1, role: "assistant", rounds: { 0: "The screen is on the Companies page." }, tools: [],
    actions: [{ id: 7, label: "Screen: industries Metals & mining", page: "Companies" }] }],
  draft: "", busy: false, setDraft: () => {}, submit: async () => {}, stop: () => {}, clear: () => {},
};

async function render(wide: boolean, onClose: () => void) {
  // jsdom has no matchMedia: the window's width as the drawer's rule reads it
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: q === "(max-width: 1200px)" && !wide, addEventListener: () => {}, removeEventListener: () => {} }));
  await act(async () => root.render(
    <AssistantPanel assistant={assistant} context={{ label: "Companies", suggestions: [] }} signedIn variant="drawer"
      inputRef={createRef<HTMLTextAreaElement>()} onClose={onClose} />,
  ));
  // the message is a lazy chunk
  await vi.waitFor(async () => {
    await act(async () => {});
    expect(container.querySelector(".act")).not.toBeNull();
  });
}

const show = () => [...container.querySelectorAll<HTMLButtonElement>(".act button")].find((b) => b.textContent === "Show");

it("over the page (a narrower window) an applied change's Show closes the panel so the page shows", async () => {
  let closed = 0;
  await render(false, () => {
    closed += 1;
  });
  expect(show()).toBeDefined();
  act(() => show()!.click());
  expect(closed).toBe(1);
});

it("beside the page (a wide window) there is no Show", async () => {
  await render(true, () => {});
  expect(container.querySelector(".act")?.textContent).toContain("Applied: Screen: industries Metals & mining");
  expect(show()).toBeUndefined();
});

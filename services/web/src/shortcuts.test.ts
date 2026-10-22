import { expect, it } from "vitest";

import { G_WINDOW_MS, shortcutReader } from "./shortcuts";

const body = () => document.body;
const key = (k: string, extra: Partial<KeyboardEvent> = {}, target: EventTarget = body()) => ({
  key: k,
  ctrlKey: false,
  metaKey: false,
  altKey: false,
  target,
  ...extra,
});

it("maps g-then-letter to each screen within the window", () => {
  let t = 0;
  const read = shortcutReader(() => t);
  const screens = { h: "home", c: "companies", b: "chart", x: "compare", a: "ask" };
  for (const [k, view] of Object.entries(screens)) {
    expect(read(key("g"))).toBeNull();
    t += 100;
    expect(read(key(k))).toEqual({ type: "go", view });
  }
});

it("ignores the letter after the window or without g", () => {
  let t = 0;
  const read = shortcutReader(() => t);
  expect(read(key("h"))).toBeNull();
  read(key("g"));
  t += G_WINDOW_MS + 1;
  expect(read(key("h"))).toBeNull();
  // a used g doesn't carry over to the next key
  read(key("g"));
  read(key("c"));
  expect(read(key("h"))).toBeNull();
});

it("opens the palette and toggles the assistant with Ctrl or Cmd, even while typing", () => {
  const read = shortcutReader();
  const input = document.createElement("textarea");
  expect(read(key("k", { ctrlKey: true }))).toEqual({ type: "palette" });
  expect(read(key("K", { metaKey: true }, input))).toEqual({ type: "palette" });
  expect(read(key("j", { ctrlKey: true }, input))).toEqual({ type: "assistant" });
});

it("leaves / and g alone while typing", () => {
  let t = 0;
  const read = shortcutReader(() => t);
  const input = document.createElement("input");
  expect(read(key("/"))).toEqual({ type: "focus-assistant" });
  expect(read(key("/", {}, input))).toBeNull();
  read(key("g", {}, input));
  expect(read(key("h"))).toBeNull();
});

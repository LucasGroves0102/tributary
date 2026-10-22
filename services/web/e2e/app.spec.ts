/** End-to-end flows against the built app (vite preview), every API call answered by
 * mockApi.ts. One worker, chromium only (playwright.config.ts). */

import { readFileSync } from "node:fs";

import { expect, test } from "@playwright/test";

import { KO, mockApi, MSFT, NVDA, PEP, startDemo } from "./mockApi";

test("try the demo lands on Home with the sandbox and its watchlist", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Tributary" })).toBeVisible();
  await page.getByRole("button", { name: "Try the demo" }).click();
  await expect(page.locator(".sandbox-banner")).toContainText("Demo sandbox: 4 companies");
  await expect(page.getByRole("heading", { name: "Home" })).toBeVisible();
  const rows = page.locator("table.watchlist tbody tr");
  await expect(rows).toHaveCount(2);
  await expect(rows.first()).toContainText("Nvidia");
  // every value carries its period and source on hover
  await expect(rows.first().locator("td.n").first()).toHaveAttribute("title", /trailing twelve months.*10-Q filed/);
});

test("screener: filter, sort and watch", async ({ page }) => {
  const fake = await mockApi(page);
  await startDemo(page);
  await page.goto("/#/companies");
  const rows = page.locator("table.screen tbody tr");
  await expect(rows).toHaveCount(4);
  await expect(rows.first()).toContainText("Microsoft"); // largest revenue first

  await page.getByRole("columnheader", { name: /Op mgn/ }).click();
  await expect(page).toHaveURL(/sort=-operating_margin_ttm/);
  await expect(rows.first()).toContainText("Nvidia");

  await page.getByLabel("Name or ticker").fill("KO");
  await page.getByLabel("Name or ticker").press("Enter");
  await expect(page).toHaveURL(/q=KO/);
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("Coca-Cola");

  await page.getByRole("button", { name: "Add Coca-Cola to watchlist" }).click();
  await expect(page.getByRole("button", { name: "Remove Coca-Cola from watchlist" })).toHaveText("✓ Watching");
  expect(fake.watched.has(KO)).toBe(true);
  expect(fake.calls).toContain(`PUT /api/query/v1/watchlist/${KO}`);

  // keyboard: the row is a tab stop; Enter opens the company
  await rows.first().focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`#/company/${KO}`));
});

test("chart builder: two metric layers and a formula, exported as CSV", async ({ page }) => {
  await mockApi(page);
  await startDemo(page);
  await page.goto("/#/chart");
  await page.getByRole("button", { name: "+ Metric" }).click();
  await page.getByRole("button", { name: "+ Metric" }).click();
  await page.getByLabel("Company", { exact: true }).nth(1).selectOption(MSFT);
  await page.getByRole("button", { name: "+ Formula" }).click();
  await page.getByLabel("Formula", { exact: true }).fill("net_income / revenue");
  await page.getByLabel("Formula", { exact: true }).press("Enter");
  await page.getByLabel("Chart title").fill("Revenue and margin");

  const legend = page.locator(".ch-legend text");
  await expect(legend).toHaveText(["KO revenue (TTM)", "MSFT revenue (TTM)", "net_income / revenue (TTM) (right axis)"]);
  await expect(page.locator(".ch-foot").first()).toContainText("Source: SEC filings");

  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Export CSV" }).click()]);
  expect(download.suggestedFilename()).toBe("revenue-and-margin.csv");
  const csv = readFileSync((await download.path())!, "utf8").split("\r\n");
  expect(csv[0]).toBe(
    "period,KO revenue (TTM) (USD),KO revenue (TTM) sources,MSFT revenue (TTM) (USD),MSFT revenue (TTM) sources," +
      "net_income / revenue (TTM) (ratio),net_income / revenue (TTM) sources",
  );
  expect(csv[1]).toMatch(/^CY2025 Q3,\d+,0000021344-26-000030,\d+,0000789019-26-000030,0\.\d+,/);

  // the same values as a table, for screen readers and copying
  await page.getByRole("button", { name: "View as table" }).click();
  await expect(page.locator(".lchart table tbody tr")).toHaveCount(4);
});

test("chart builder: formula autocomplete, annual frequency and deck output", async ({ page }) => {
  await mockApi(page);
  await startDemo(page);
  await page.goto("/#/chart");
  await page.getByRole("button", { name: "+ Metric" }).click();
  await page.getByRole("button", { name: "+ Formula" }).click();
  const formula = page.getByLabel("Formula", { exact: true });
  await formula.click();
  await formula.pressSequentially("net_inc");
  const suggestions = page.getByRole("listbox", { name: "Formula suggestions" });
  await expect(suggestions.getByRole("option").first()).toContainText("net_income");
  await formula.press("Tab");
  await expect(formula).toHaveValue("net_income");
  await formula.pressSequentially(" ");
  await suggestions.getByRole("option", { name: /÷/ }).click();
  await formula.pressSequentially("revenue");
  await formula.press("Enter");
  await expect(page.locator(".ch-legend text")).toHaveCount(2);

  await page.getByRole("button", { name: "Annual" }).click();
  await expect(page.locator(".ch-axis text")).toHaveText(["CY2025"]); // the mock's one full calendar year
  await page.getByLabel("Deck output").check();
  await expect(page.locator(".lchart.deck")).toBeVisible();
  await expect(page.locator(".ch-sub")).toHaveText(/^Calendar years, CY2025 · USD \(left\), % \(right\)$/);
  await expect(page.locator(".ch-foot").first()).toContainText("Source: SEC filings via Tributary; 10-Q for KO; latest KO 10-Q filed Jul 30, 2026; as of Oct 21, 2026.");
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Export SVG" }).click()]);
  const svg = readFileSync((await download.path())!, "utf8");
  expect(svg).toContain("Source: SEC filings via Tributary");
  expect(svg).toContain("Calendar years, CY2025");
});

test("comps: target and peers with the peer median, exported as CSV", async ({ page }) => {
  await mockApi(page);
  await startDemo(page);
  await page.goto(`/#/compare?target=${PEP}&peers=${KO},${NVDA}`);
  const rows = page.locator("table.comps tbody tr");
  await expect(rows).toHaveCount(5); // 3 companies, median, high/low
  await expect(rows.first()).toContainText("PepsiCo");
  await expect(rows.first()).toContainText("target");
  await expect(rows.nth(3)).toContainText("Peer median");
  await expect(page.getByText("Suggested", { exact: true })).toBeVisible();
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Export CSV" }).click()]);
  expect(download.suggestedFilename()).toBe("comps-pep.csv");
  const csv = readFileSync((await download.path())!, "utf8");
  expect(csv).toContain(`target,${PEP},PEP,PepsiCo,financials`);
  expect(csv).toContain("peer median (excludes target)");
});

test("save a chart to a new workspace and reopen it", async ({ page }) => {
  const fake = await mockApi(page);
  await startDemo(page);
  await page.goto("/#/chart");
  await page.getByRole("button", { name: "Margins" }).click();
  await expect(page.locator(".ch-legend text")).not.toHaveCount(0);

  await page.getByRole("button", { name: "Save…" }).click();
  const form = page.getByRole("form", { name: "Save chart" });
  await form.getByLabel("Name").fill("Margin check");
  await form.getByLabel("Workspace", { exact: true }).selectOption({ label: "New workspace…" });
  await form.getByLabel("New workspace").fill("Beverages");
  await form.getByRole("button", { name: "Save" }).click();
  await expect(page.locator(".savebar")).toContainText("Beverages / Margin check");
  expect([...fake.items.values()].map((i) => i.name)).toEqual(["Margin check"]);

  // start over, then reopen it from the sidebar
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await page.getByRole("button", { name: "Leverage" }).click();
  await expect(page.getByLabel("Chart title")).toHaveValue("Debt to equity, quarter end");
  await page.locator('button[title="Open in Chart builder: Margin check"]').click();
  await expect(page.getByLabel("Chart title")).toHaveValue("Operating margin, TTM");
  await expect(page.locator(".savebar")).toContainText("Margin check");
});

test("assistant drawer opens, answers from a streamed reply, and closes", async ({ page }) => {
  const fake = await mockApi(page);
  await startDemo(page);
  await page.goto("/#/companies");
  const drawer = page.locator("aside.ai-drawer");
  if (await drawer.isVisible()) await page.getByRole("button", { name: "Close assistant" }).click();
  await expect(drawer).toHaveCount(0);
  await page.getByRole("button", { name: /Assistant/ }).first().click();
  await expect(drawer).toBeVisible();
  const box = page.getByLabel("Ask the assistant");
  await expect(box).toBeFocused();
  await box.fill("What was PepsiCo's revenue?");
  await box.press("Enter");
  await expect(drawer).toContainText("$91.9B");
  expect(fake.asked).toEqual(["What was PepsiCo's revenue?"]);
  await page.getByRole("button", { name: "Close assistant" }).click();
  await expect(drawer).toHaveCount(0);
});

test("narrow screens: the sidebar is a drawer behind Menu", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page);
  await startDemo(page);
  const side = page.locator("#side-nav");
  await expect(side).toBeHidden();
  await page.getByRole("button", { name: "Menu" }).click();
  await expect(side).toBeVisible();
  await side.getByRole("link", { name: /Companies/ }).click();
  await expect(side).toBeHidden();
  await expect(page.locator("table.screen tbody tr")).toHaveCount(4);
});

test("data notes: recent ones on Home, the full list on the company's tab", async ({ page }) => {
  await mockApi(page);
  await startDemo(page);
  const card = page.locator("section.card", { has: page.getByRole("heading", { name: "Recent data notes for your companies" }) });
  await expect(card.locator(".note")).toContainText("Revenue restated");
  await card.locator(".note-t a").first().click();
  await expect(page).toHaveURL(new RegExp(`#/company/${PEP}/notes`));
  await expect(page.locator('.tabs a[aria-current="page"]')).toHaveText("Data notes");
  await expect(page.locator(".notes-group h3")).toHaveText(["Restated figures (1)"]);
  await expect(page.locator(".note p")).toContainText("first reported in the 10-Q filed Jul 10, 2025 and changed by −1.2%");
  await expect(page.locator(".note-f a").first()).toHaveAttribute("title", /10-Q 0000077476-25-000020/);
});

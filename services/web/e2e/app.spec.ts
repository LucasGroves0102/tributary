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
  // a demo sandbox screens only its own companies
  await expect(page.getByText("4 companies in your demo")).toBeVisible();
  await expect(page.getByText("Only companies I can read")).toHaveCount(0);
  expect(fake.calls.find((c) => c.includes("/v1/screener?"))).toContain("access=financials");

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

  // side by side: a small chart per column, each company a line
  await page.getByRole("button", { name: /^Show \d+ charts$/ }).click();
  const multiples = page.locator(".multiple");
  await expect(multiples).toHaveCount(8);
  await expect(multiples.first().locator(".ch-title")).toHaveText("Revenue, TTM");
  await expect(multiples.first().locator(".ch-legend text")).toHaveText(["PEP", "KO", "NVDA"]);
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

test("keyboard: ? lists the shortcuts, Ctrl+arrows jump in tables; the limits page", async ({ page }) => {
  await mockApi(page);
  await startDemo(page);
  await page.goto("/#/companies");
  const rows = page.locator("table.screen tbody tr");
  await expect(rows).toHaveCount(4);
  await rows.first().focus();
  await page.keyboard.press("Control+ArrowDown");
  await expect(rows.last()).toBeFocused();
  await page.keyboard.press("Control+ArrowRight");
  await expect(rows.last().locator("> td, > th").last()).toBeFocused();
  await page.keyboard.press("Control+ArrowUp");
  await expect(rows.first().locator("> td, > th").last()).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(rows.first()).toBeFocused();

  await page.keyboard.press("?");
  const sheet = page.getByRole("dialog", { name: "Keyboard shortcuts" });
  await expect(sheet).toBeVisible();
  await expect(sheet).toContainText("First or last column");
  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();

  await page.locator(".side a.cover-link").click();
  await expect(page.getByRole("heading", { name: "What Tributary covers" })).toBeVisible();
  await expect(page.locator(".about")).toContainText("No consensus estimates");
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

test("reports: write one from a workspace, read it with its charts, download and print", async ({ page }) => {
  const fake = await mockApi(page);
  await startDemo(page);
  fake.workspaces.push({ workspace_id: "w-bev", name: "Beverages", created_at: "2026-10-20T00:00:00Z", updated_at: "2026-10-20T00:00:00Z" });
  fake.items.set("i-chart", {
    item_id: "i-chart", workspace_id: "w-bev", kind: "chart", name: "PEP margins", created_at: "2026-10-20T00:00:00Z",
    updated_at: "2026-10-20T00:00:00Z", opened_at: null,
    state: { title: "PEP operating margin", range: 3, align: "calendar", layers: [{ kind: "metric", companyId: PEP, metric: "operating_margin", transform: "ttm", type: "line", axis: "L", hidden: false }] },
  });
  fake.items.set("i-comps", {
    item_id: "i-comps", workspace_id: "w-bev", kind: "comps", name: "PEP vs KO", created_at: "2026-10-20T00:00:01Z",
    updated_at: "2026-10-20T00:00:01Z", opened_at: null, state: { target: PEP, peers: [KO], cols: ["revenue_ttm", "operating_margin_ttm"] },
  });
  await page.goto("/#/workspace/w-bev");
  await expect(page.getByRole("heading", { name: "Beverages" })).toBeVisible();
  await expect(page.locator(".page")).toContainText("counts as 3 assistant questions");
  await page.getByRole("button", { name: "Write a report" }).click();

  await expect(page).toHaveURL(/#\/report\/r\d+$/);
  expect(fake.reports).toEqual([{ workspace_id: "w-bev" }]);
  const report = page.locator(".report-doc");
  await expect(report.getByRole("heading", { level: 1 })).toHaveText("Beverages: margins hold");
  // each saved item drawn in place, from its saved state, with live data
  await expect(report.locator(".report-embed")).toHaveCount(2);
  await expect(report.locator('.report-embed[aria-label="PEP margins"] .ch-legend text')).not.toHaveCount(0);
  await expect(report.locator('.report-embed[aria-label="PEP vs KO"] .multiple')).toHaveCount(2);
  // the unverified figure is flagged; the cited filing links to EDGAR
  await expect(report.locator(".figure.unverified")).toHaveText("$99.9B");
  await expect(report.locator(".report-meta")).toContainText("1 of 2 figures unverified");
  await expect(report.locator(`a.accession[href^="https://www.sec.gov/"]`).first()).toBeVisible();

  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Download Markdown" }).click()]);
  expect(download.suggestedFilename()).toBe("report-beverages.md");
  const md = readFileSync((await download.path())!, "utf8");
  expect(md).toContain("$99.9B [unverified]");
  expect(md).toContain("> **PEP margins**: Chart");

  // print: the report alone, light palette whatever the theme
  await page.emulateMedia({ media: "print" });
  await expect(page.locator("#side-nav")).toBeHidden();
  await expect(page.getByRole("button", { name: "Write again" })).toBeHidden();
  const bg = await report.locator(".report-embed").first().evaluate((e) => getComputedStyle(e).backgroundColor);
  expect(bg).toBe("rgb(255, 255, 255)");
  await page.emulateMedia({ media: "screen" });

  // the report is listed in its workspace and the sidebar
  await page.goto("/#/workspace/w-bev");
  await expect(page.locator(".ws-list").first()).toContainText("Report: Beverages");
});

test("customizable views: Home arrangement and table columns, saved and reset", async ({ page }) => {
  const fake = await mockApi(page);
  await startDemo(page);
  const titles = page.locator(".page section.card h2");
  await expect(titles.first()).toHaveText("Continue where you left off");
  await page.getByRole("button", { name: "Customize Home" }).click();
  const editor = page.getByRole("region", { name: "Customize Home" });
  await editor.getByRole("checkbox", { name: "Status strip" }).uncheck();
  await editor.getByRole("button", { name: "Move Watchlist up" }).click();
  await page.getByRole("button", { name: "Done" }).click();
  await expect(page.locator(".status-strip")).toHaveCount(0);
  await expect(titles.first()).toHaveText("Watchlist");
  expect((fake.views.home as { cards: unknown[] }).cards.slice(0, 2)).toEqual([{ id: "status", hidden: true }, { id: "watchlist", hidden: false }]);

  // watchlist columns
  const watch = page.locator("section.card", { has: page.getByRole("heading", { name: "Watchlist" }) });
  await watch.getByRole("button", { name: "Columns" }).click();
  await page.getByLabel("Add a watchlist column").selectOption({ label: "Gross margin (TTM)" });
  await page.getByRole("button", { name: "Remove Net debt (qtr end)" }).click();
  await expect(page.locator("table.watchlist thead")).toContainText("Gross mgn");
  await expect(page.locator("table.watchlist thead")).not.toContainText("Net debt");

  // it's all there after a reload
  await page.reload();
  await expect(page.locator(".sandbox-banner")).toBeVisible();
  await expect(titles.first()).toHaveText("Watchlist");
  await expect(page.locator("table.watchlist thead")).toContainText("Gross mgn");
  expect(fake.calls.some((c) => c.startsWith("GET /api/query/v1/watchlist?") && c.includes("columns=gross_margin_ttm"))).toBe(true);

  // screener columns, then reset
  await page.goto("/#/companies");
  await page.getByRole("button", { name: "Columns" }).click();
  await page.getByLabel("Add a screener column").selectOption({ label: "Net margin (TTM)" });
  await expect(page.locator("table.screen thead")).toContainText("Net margin");
  expect(fake.views.screener).toEqual({ columns: [
    "revenue_ttm", "revenue_growth_ttm", "gross_margin_ttm", "operating_margin_ttm", "free_cash_flow_ttm", "net_debt_q", "debt_to_equity_q", "net_margin_ttm",
  ] });
  await page.getByRole("button", { name: "Reset" }).click();
  await expect(page.locator("table.screen thead")).not.toContainText("Net margin");
  expect(fake.views.screener).toBeUndefined();
});

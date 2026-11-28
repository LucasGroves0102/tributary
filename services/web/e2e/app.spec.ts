/** End-to-end flows against the built app (vite preview), every API call answered by
 * mockApi.ts. One worker, chromium only (playwright.config.ts). */

import { readFileSync } from "node:fs";

import { expect, test } from "@playwright/test";

import { byId, CONNECT_REDIRECT, GITHUB_SESSION, KO, mockApi, NEW_API_KEY, NVDA, PEP, screenerRow, startDemo } from "./mockApi";

test("try the demo lands on Home with the sandbox and its watchlist", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Tributary" })).toBeVisible();
  await page.getByRole("button", { name: "Try the demo" }).click();
  // the sandbox is the top bar's account chip (spec §4): what it covers in its title
  await expect(page.locator(".top .who")).toContainText(/Demo sandbox\s· resets in/);
  await expect(page.locator(".top .who button")).toHaveAttribute("title", /\(4 companies\)/);
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
  // (a company_id list is the name directory asking for identity, not this screen)
  expect(fake.calls.find((c) => c.includes("/v1/screener?") && !c.includes("company_id="))).toContain("access=financials");

  // the header's button sorts (Marek 14: a keyboard reaches it)
  await page.getByRole("columnheader", { name: /Op mgn/ }).getByRole("button").click();
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
  // a layer's company is the shared search, not a list loaded into the browser
  await page.getByRole("button", { name: /^Company: / }).nth(1).click();
  const pick = page.getByRole("combobox", { name: "Company", exact: true });
  await pick.fill("MSFT");
  await expect(page.getByRole("listbox", { name: "Company results" }).getByRole("option").first()).toContainText("MSFT");
  await pick.press("Enter");
  await page.getByRole("button", { name: "+ Formula" }).click();
  await page.getByLabel("Formula", { exact: true }).fill("net_income / revenue");
  await page.getByLabel("Formula", { exact: true }).press("Enter");
  await page.getByLabel("Chart title").fill("Revenue and margin");

  const legend = page.locator(".lchart-svg .ch-legend text");
  // the first layer starts from the watchlist's first company
  await expect(legend).toHaveText(["NVDA revenue (TTM)", "MSFT revenue (TTM)", "net income ÷ revenue (TTM, right axis)"]);
  await expect(page.locator(".ch-foot").first()).toContainText("Source: SEC filings");

  // the bar's exports are one menu (Riley 101: three buttons wrapped the toolbar)
  await page.getByRole("button", { name: "Export", exact: true }).click();
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("menuitem", { name: "CSV, exact values" }).click()]);
  expect(download.suggestedFilename()).toBe("revenue-and-margin.csv");
  const csv = readFileSync((await download.path())!, "utf8").split("\r\n");
  expect(csv[0]).toBe(
    "period,NVDA revenue (TTM) (USD),NVDA revenue (TTM) sources,MSFT revenue (TTM) (USD),MSFT revenue (TTM) sources," +
      "net income ÷ revenue (TTM) (ratio),net income ÷ revenue (TTM) sources",
  );
  expect(csv[1]).toMatch(/^CY2025 Q3,\d+,0001045810-26-000030,\d+,0000789019-26-000030,0\.\d+,/);

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
  await expect(suggestions).toHaveCount(0); // a complete name: nothing more to offer over the controls
  await formula.pressSequentially("/ revenue");
  await formula.press("Enter");
  await expect(page.locator(".lchart-svg .ch-legend text")).toHaveCount(2);

  await page.getByRole("button", { name: "Annual" }).click();
  // the mock's one full calendar year, then the year in progress, marked (F5)
  await expect(page.locator(".lchart-svg .ch-axis text")).toHaveText(["CY2025", "CY2026 Q2*"]);
  await page.getByRole("button", { name: "Show" }).click();
  await page.getByRole("menuitemcheckbox", { name: "Deck output" }).click();
  await page.keyboard.press("Escape");
  await expect(page.locator(".lchart.deck")).toBeVisible();
  await expect(page.locator(".lchart-svg .ch-sub")).toHaveText(/^Calendar years, CY2025–CY2026 Q2\* · USD \(left\), % \(right\)$/);
  await expect(page.locator(".ch-foot").first()).toContainText("Source: SEC filings via Tributary; 10-Q for NVDA; latest NVDA 10-Q filed Jul 30, 2026; as of Oct 21, 2026.");
  await page.getByRole("button", { name: "Export", exact: true }).click();
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("menuitem", { name: "SVG, for slides" }).click()]);
  const svg = readFileSync((await download.path())!, "utf8");
  expect(svg).toContain("Source: SEC filings via Tributary");
  expect(svg).toContain("Calendar years, CY2025–CY2026 Q2*");
  expect(svg).toContain("* CY2026 Q2: the latest quarter, not a full calendar year");
});

test("comps: target and peers with the peer median, exported as CSV", async ({ page }) => {
  await mockApi(page);
  await startDemo(page);
  await page.goto(`/#/compare?target=${PEP}&peers=${KO},${NVDA}`);
  const rows = page.locator("table.comps tbody tr");
  await expect(rows).toHaveCount(6); // 3 companies, median, high, low
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
  // #199: eight charts in rows of three: the last row is centred, every chart the same width
  const boxes = await multiples.evaluateAll((els) => els.map((e) => e.getBoundingClientRect()));
  const lines = [...new Set(boxes.map((b) => Math.round(b.y)))];
  expect(lines).toHaveLength(3);
  expect(new Set(boxes.map((b) => Math.round(b.width))).size).toBe(1);
  const grid = (await page.locator(".multiples").boundingBox())!;
  const last = boxes.slice(6);
  const left = last[0]!.x - grid.x;
  const right = grid.x + grid.width - (last[1]!.x + last[1]!.width);
  expect(Math.abs(left - right)).toBeLessThan(2);
  // the instruction is for screen readers only, not printed on eight charts
  const hints = await multiples.getByText("Click a point to open its filing").evaluateAll((els) => els.map((e) => e.getBoundingClientRect().width));
  expect(hints).toHaveLength(8);
  expect(hints.every((w) => w <= 1)).toBe(true);
});

test("save a chart to a new workspace and reopen it", async ({ page }) => {
  const fake = await mockApi(page);
  await startDemo(page);
  await page.goto("/#/chart");
  await page.getByRole("button", { name: /from a template/ }).click();
  await page.getByRole("menuitem", { name: "Margins" }).click();
  await expect(page.locator(".ch-legend text")).not.toHaveCount(0);

  await page.getByRole("button", { name: "Save…" }).click();
  const form = page.getByRole("form", { name: "Save chart" });
  await form.getByLabel("Name").fill("Margin check");
  await form.getByLabel("Workspace", { exact: true }).selectOption({ label: "New workspace…" });
  await form.getByLabel("New workspace").fill("Beverages");
  await form.getByRole("button", { name: "Save" }).click();
  await expect(page.locator(".savebar")).toContainText("Chart in Beverages: Margin check");
  expect([...fake.items.values()].map((i) => i.name)).toEqual(["Margin check"]);

  // start over, then reopen it from the sidebar
  // Save as and Close are in the bar's More (Riley, test 10, finding 86)
  await page.locator(".savebar .more-btn").click();
  await page.getByRole("menuitem", { name: /^Close/ }).click();
  await page.getByRole("button", { name: /with a template/ }).click();
  await page.getByRole("menuitem", { name: "Leverage" }).click();
  await expect(page.getByLabel("Chart title")).toHaveValue(""); // written from the layers
  await expect(page.locator(".lchart-svg .ch-title").first()).toContainText("Debt / equity");
  await page.locator('button[title="Open in Chart builder: Margin check"]').click();
  await expect(page.locator(".lchart-svg .ch-title").first()).toContainText("Operating margin");
  await expect(page.locator(".savebar")).toContainText("Margin check");
});

test("a saved screen watched: entering, leaving and a line; marked; its alerts link back to it", async ({ page }) => {
  const fake = await mockApi(page);
  await startDemo(page);
  await page.goto("/#/companies?margin=20");
  await expect(page.locator("table.screen tbody tr").first()).toBeVisible();
  await page.getByRole("button", { name: "Save…" }).click();
  const form = page.getByRole("form", { name: "Save screen" });
  await form.getByLabel("Name").fill("Cheap growers");
  await form.getByRole("button", { name: "Save" }).click();
  await expect(page.locator(".savebar")).toContainText("Cheap growers");
  const item = [...fake.items.values()].find((i) => i.kind === "screen")!;

  // watch it: companies entering and leaving, and operating margin falling below 15% for any company
  const strip = page.getByRole("region", { name: "Alerts for the saved screen Cheap growers" });
  await strip.getByRole("button", { name: "Watch…" }).click();
  await strip.getByRole("button", { name: "Add a line" }).click();
  await strip.getByLabel("Column").selectOption({ label: "Operating margin (TTM)" });
  await strip.getByRole("radio", { name: "Falls below" }).click();
  await strip.getByLabel("Value in %").fill("15");
  await expect(strip.locator(".watch-reads")).toHaveText("Alerts when a company's Operating margin (TTM) falls below 15.0%");
  await page.screenshot({ path: test.info().outputPath("watch-form.png") });
  await strip.getByRole("button", { name: "Start watching" }).click();
  await expect(strip.locator(".watch-row")).toContainText("Watching companies entering, companies leaving and Operating margin (TTM) below 15.0%");
  const watch = fake.watches.get(String(item.item_id))!;
  expect(watch.lines).toEqual([{ column: "operating_margin_ttm", label: "Operating margin (TTM)", direction: "below", threshold: "0.15", company_id: null }]);
  expect(watch.columns).toEqual(expect.arrayContaining(["revenue_ttm", "operating_margin_ttm"]));
  // listed under Companies and marked in the sidebar's workspaces
  await expect(page.getByRole("navigation", { name: "Your saved screens" })).toContainText("Cheap growers watched");
  await expect(page.locator(".ws-watched")).toHaveCount(1);

  // an alert after the night's figures, on the Alerts page, with a link back to the screen
  fake.screenAlerts = [{ id: "a1", watch_id: watch.id, item_id: item.item_id, name: "Cheap growers", hidden: 0,
    created_at: "2026-10-21T10:40:00Z", read_at: null, figures_at: "2026-10-21T10:31:00Z",
    crossed: [{ line: 0, column: "operating_margin_ttm", label: "Operating margin (TTM)", direction: "below", threshold: "0.15",
      company_id: null, unit: "ratio", display: "percent", companies: [{ company_id: KO, value: "0.142" }], more: 0 }],
    entered: { companies: [PEP], more: 0 } }];
  await page.goto("/#/alerts");
  const alert = page.locator(".alert-item", { hasText: "Cheap growers" });
  await expect(alert).toContainText("Operating margin (TTM) fell below 15.0%");
  await expect(alert).toContainText("14.2%");
  await expect(alert).toContainText("Entered:");
  await page.screenshot({ path: test.info().outputPath("screen-alert.png"), fullPage: true });
  await alert.getByRole("link", { name: "Open the screen" }).click();
  await expect(page).toHaveURL(/#\/companies\?margin=20$/);
  await expect(page.locator(".savebar")).toContainText("Cheap growers");
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

  await page.locator('.side a.cover-link[href="#/about"]').click();
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
  await page.getByRole("button", { name: "Write it" }).click();

  await expect(page).toHaveURL(/#\/report\/r\d+$/);
  expect(fake.reports).toEqual([{ workspace_id: "w-bev" }]);
  const report = page.locator(".report-doc");
  await expect(report.getByRole("heading", { level: 1 })).toHaveText("Beverages: margins hold");
  // each saved item drawn in place, from its saved state, with live data
  await expect(report.locator(".report-embed")).toHaveCount(2);
  await expect(report.locator('.report-embed[aria-label="PEP margins"] .ch-legend text')).not.toHaveCount(0);
  // comps stay a table (F6), never charts
  await expect(report.locator('.report-embed[aria-label="PEP vs KO"] table.comps tbody tr:not(.agg)')).toHaveCount(2);
  await expect(report.locator('.report-embed[aria-label="PEP vs KO"] .multiple')).toHaveCount(0);
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
  // the start card leads (F6, #127); then the cards: the watchlist first, the status strip off
  const titles = page.locator(".page section.card:not(.start-card) h2");
  await expect(page.locator(".start-card h2")).toHaveText("Start an analysis");
  await expect(titles.first()).toHaveText("Watchlist");
  await expect(page.locator(".status-strip")).toHaveCount(0);
  await page.getByRole("button", { name: "Customize Home" }).click();
  const editor = page.getByRole("region", { name: "Customize Home" });
  await editor.getByRole("checkbox", { name: "Continue where you left off" }).check();
  await editor.getByRole("button", { name: "Move Continue where you left off up" }).click();
  await editor.getByRole("button", { name: "Move Continue where you left off up" }).click();
  await editor.getByRole("button", { name: "Move Continue where you left off up" }).click();
  await page.getByRole("button", { name: "Done" }).click();
  await expect(titles.first()).toHaveText("Continue where you left off");
  expect((fake.views.home as { cards: unknown[] }).cards.slice(0, 2)).toEqual([{ id: "recent", hidden: false }, { id: "watchlist", hidden: false }]);

  // watchlist columns
  const watch = page.locator("section.card", { has: page.getByRole("heading", { name: "Watchlist" }) });
  await watch.getByRole("button", { name: "Columns" }).click();
  await page.getByLabel("Add a watchlist column").fill("gross");
  await page.getByRole("button", { name: "+ Gross margin (TTM)" }).click();
  await page.getByRole("button", { name: "Remove Net debt (qtr end)" }).click();
  await expect(page.locator("table.watchlist thead")).toContainText("Gross mgn");
  await expect(page.locator("table.watchlist thead")).not.toContainText("Net debt");

  // it's all there after a reload
  await page.reload();
  await expect(page.locator(".top .who")).toBeVisible();
  await expect(titles.first()).toHaveText("Continue where you left off");
  await expect(page.locator("table.watchlist thead")).toContainText("Gross mgn");
  expect(fake.calls.some((c) => c.startsWith("GET /api/query/v1/watchlist?") && c.includes("columns=gross_margin_ttm"))).toBe(true);

  // screener columns, then reset
  await page.goto("/#/companies");
  await page.getByRole("button", { name: "Columns" }).click();
  await page.getByLabel("Add a screener column").fill("net margin");
  await page.getByRole("button", { name: "+ Net margin (TTM)" }).click();
  await expect(page.locator("table.screen thead")).toContainText("Net margin");
  expect(fake.views.screener).toEqual({ columns: [
    "revenue_ttm", "revenue_growth_ttm", "gross_margin_ttm", "operating_margin_ttm", "free_cash_flow_ttm", "net_debt_q", "debt_to_equity_q", "net_margin_ttm",
  ] });
  await page.getByRole("button", { name: "Reset", exact: true }).click();
  await expect(page.locator("table.screen thead")).not.toContainText("Net margin");
  expect(fake.views.screener).toBeUndefined();
});

test("a first visit starts with the assistant closed, and nothing floats over the page", async ({ page }) => {
  await mockApi(page);
  await startDemo(page);
  await expect(page.locator("aside.ai-drawer")).toHaveCount(0);
  await expect(page.locator(".fab")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Assistant/ })).toBeVisible();
});

test("#68: the main screens load with a clean console", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("response", (r) => {
    if (r.status() >= 400) errors.push(`${r.status()} ${new URL(r.url()).pathname}`);
  });
  await mockApi(page);
  await startDemo(page);
  errors.length = 0; // signed in from here
  for (const hash of ["#/", "#/companies", `#/company/${NVDA}`, `#/company/${NVDA}/statements`, `#/compare?target=${PEP}&peers=${KO}`, "#/chart", "#/analysis", "#/alerts", "#/portfolio", "#/portfolio/import", "#/data", "#/settings", "#/status", "#/api"]) {
    await page.goto(`/${hash}`);
    await page.waitForLoadState("networkidle");
  }
  expect(errors).toEqual([]);
});

test("no screen scrolls sideways, on a phone, at 1024 or at 1440 with the panel open", async ({ page }) => {
  await mockApi(page);
  await startDemo(page);
  const screens = ["#/", "#/companies", `#/company/${NVDA}`, `#/company/${NVDA}/statements`, `#/company/${NVDA}/peers`, `#/compare?target=${PEP}&peers=${KO}`, "#/chart", "#/portfolio", "#/data"];
  for (const [width, height, panel] of [[390, 844, false], [1024, 768, false], [1440, 900, true]] as const) {
    await page.setViewportSize({ width, height });
    await page.evaluate((open) => localStorage.setItem("tributary.assistant", open ? "open" : "closed"), panel);
    await page.reload();
    await expect(page.locator("aside.ai-drawer")).toHaveCount(panel ? 1 : 0);
    for (const hash of screens) {
      await page.goto(`/${hash}`);
      await page.waitForLoadState("networkidle");
      const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(over, `${hash} at ${width}`).toBe(0);
    }
  }
});

test("with the panel open at 1440 the screener still shows its numbers, under a frozen name", async ({ page }) => {
  await mockApi(page);
  await startDemo(page);
  await page.evaluate(() => localStorage.setItem("tributary.assistant", "open"));
  await page.goto("/#/companies");
  await page.reload();
  await expect(page.locator("aside.ai-drawer")).toBeVisible();
  const row = page.locator("table.screen tbody tr").first();
  const box = await page.locator(".tw").first().boundingBox();
  const revenue = await row.locator("td.n").first().boundingBox();
  expect(revenue!.x + revenue!.width).toBeLessThan(box!.x + box!.width);
  await expect(row.locator("td.first")).toHaveCSS("position", "sticky");
});

test("workspace actions: from the page, with a confirmation over the whole screen", async ({ page }) => {
  await mockApi(page);
  await startDemo(page);
  await page.goto("/#/chart");
  await page.getByRole("button", { name: /from a template/ }).click();
  await page.getByRole("menuitem", { name: "Margins", exact: true }).click();
  await page.getByRole("button", { name: "Save…" }).click();
  const form = page.locator(".save-form");
  await form.getByLabel("Name").fill("Margin check");
  await form.getByLabel("Workspace", { exact: true }).selectOption({ label: "New workspace…" });
  await form.getByLabel("New workspace").fill("Beverages");
  await form.getByRole("button", { name: "Save" }).click();
  await page.locator(".side a.ws-open", { hasText: "Beverages" }).click();
  await expect(page.getByRole("heading", { name: "Beverages" })).toBeVisible();
  const main = page.locator("main.content");
  await main.getByRole("button", { name: "Delete", exact: true }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("Delete “Beverages” and its 1 item?");
  await expect(dialog.getByRole("button", { name: "Keep" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
});

test("a company reporting in euros: labelled everywhere, never ranked or averaged with dollars (056)", async ({ page }) => {
  await mockApi(page);
  // Coca-Cola's mock answers, reshaped as a 20-F filer in euros that reports once a year
  const euro = (row: Record<string, unknown>) => ({
    ...row,
    values: { ...(row.values as Record<string, unknown>), revenue_ttm: "2097429000", free_cash_flow_ttm: "307573000", net_debt_q: "816076000" },
    readings: Object.fromEntries(["revenue_ttm", "free_cash_flow_ttm", "net_debt_q"].map((k) => [k, {
      status: "other_currency", unit: "EUR", reason: "reported in EUR, so it isn't sorted, filtered or averaged with US-dollar figures" }])),
  });
  await page.route("**/api/query/v1/screener?**", async (route) => {
    const ids = new URL(route.request().url()).searchParams.getAll("company_id");
    if (!ids.includes(KO)) return route.fallback();
    const rows = ids.map((id) => byId.get(id)).filter((c) => !!c).map((c) => (c!.id === KO ? euro(screenerRow(c!)) : screenerRow(c!)));
    return route.fulfill({ json: { as_of: "", total: rows.length, limit: 50, offset: 0, sort: "name", columns: [], rows } });
  });
  await page.route(`**/api/query/v1/companies/${KO}/financials?**`, async (route) => {
    const view = new URL(route.request().url()).searchParams.get("view");
    const row = (metric: string, end: string, value: string, fiscal: string) => ({
      metric, period_start: metric === "total_assets" ? null : `${Number(end.slice(0, 4)) - 1}-10-01`, period_end: end,
      fiscal_period: fiscal, calendar_period: null, value, unit: "EUR", value_source: "ttm",
      citation: { accession_no: `${KO}-25-000001`, form: "20-F", filed: "2025-12-18", xbrl_tag: "ifrs-full:Revenue", source_accessions: null, filing_url: null },
      freshness: { filed: "2025-12-18", ingested_at: "2025-12-19T00:00:00Z" },
    });
    const rows = view === "ttm"
      ? [row("revenue", "2025-09-30", "2097429000", "FY2025 Q4"), row("revenue", "2024-09-30", "1804690000", "FY2024 Q4"),
         row("net_income", "2025-09-30", "348327000", "FY2025 Q4"), row("net_income", "2024-09-30", "191603000", "FY2024 Q4")]
      : [row("total_assets", "2025-09-30", "4942120000", "FY2025 Q4")];
    await route.fulfill({ json: { company_id: KO, view, basis: "fiscal", as_of: "", currency: "EUR", cadence: "annual", rows } });
  });
  await startDemo(page);
  await page.goto(`/#/company/${KO}`);
  // Seal: the header's meta row is separate facts in sentence case
  await expect(page.locator(".co-meta").first()).toContainText("Figures in EUR");
  await expect(page.getByText("€2.10B").first()).toBeVisible();
  await page.screenshot({ path: test.info().outputPath("euro-company.png") });

  await page.goto(`/#/company/${KO}/statements`);
  await expect(page.getByText(/reports once a year/)).toBeVisible();
  await expect(page.getByText(/Figures in EUR, as/)).toBeVisible();
  await page.getByRole("button", { name: "Show Annual" }).click();
  await expect(page.locator("table").first()).toContainText("€2.10B");
  await page.screenshot({ path: test.info().outputPath("euro-statements.png"), fullPage: true });

  // in a comps table its revenue shows in euros and stays out of the dollar median
  await page.goto(`/#/compare?target=${PEP}&peers=${KO},${NVDA}`);
  await expect(page.locator("tr", { hasText: "Coca-Cola" }).first()).toContainText("€2.10B");
  await expect(page.locator("td[title*='in another currency']").first()).toBeAttached();
  await page.screenshot({ path: test.info().outputPath("euro-compare.png"), fullPage: true });
});

test("a thesis check says what it costs before it runs, and links a claim of the user's own model", async ({ page }) => {
  // analyst test 4: the cost and time were said only afterwards (finding 22); the user's own
  // model's figures read "not in the data" (finding 9)
  const fake = await mockApi(page);
  fake.workspaces.push({ workspace_id: "w1", name: "Beverages", created_at: "2026-10-21T00:00:00Z", updated_at: "2026-10-21T00:00:00Z" });
  fake.items.set("t1", {
    item_id: "t1", workspace_id: "w1", kind: "thesis", name: "PepsiCo thesis", created_at: "2026-10-21T00:00:00Z",
    updated_at: "2026-10-21T00:00:00Z", opened_at: null,
    state: { companyId: PEP, markdown: "# PepsiCo\n\nRevenue was $91.9B over the last twelve months.\n\nMy base case puts FY2027 revenue at $99.9B.\n" },
  });
  await startDemo(page);
  await page.goto("/#/thesis/t1");
  // agent-api's numbers (/v1/allowance), beside the button before anything runs
  await expect(page.locator(".check-cost-inline")).toHaveText("Uses 4 of your 30 questions · about 2 min");
  const check = page.getByRole("button", { name: "Check my thesis" });
  await expect(check).toHaveAttribute("title", /A check counts as 4 questions \(you have 30 left this hour, 100 today\) and usually takes about 2 minutes\./);
  await check.click();
  const mine = page.locator(".claim.model");
  await expect(mine.locator(".status-badge")).toHaveText("Your model");
  await expect(mine.locator(".check-model-match a")).toHaveAttribute("href", "#/model/m1");
  await expect(mine.locator(".check-model-match li")).toHaveText("FY2027E revenue $99.90B");
  await expect(page.locator(".check-panel .badge.ok")).toHaveText("✓ Every figure and filing traced to data the assistant read or to your model");
  await expect(page.locator(".check-panel .check-cost")).toContainText("A check counts as 4 questions");
  await expect(page.locator(".check-cost-inline")).toHaveCount(0);
});

test("an earnings release's guidance table reads as a table (parser v7, analyst test 4 finding 31)", async ({ page }) => {
  await mockApi(page);
  await startDemo(page);
  await page.goto(`/#/company/${KO}/earnings`);
  const grid = page.locator("table.release-grid").first();
  await expect(grid).toBeVisible();
  await expect(grid.locator("thead th")).toHaveText(["", "Current", "Prior"]);
  await expect(grid.locator("tbody tr").first().locator("td")).toHaveText(["Organic revenues (non-GAAP)", "At least 5% growth", "Approx. 5% growth"]);
  // the heading stays a line above it; nothing reads as alternating label and value lines
  await expect(page.locator(".release-block .rl.heading")).toHaveText("Full Year 2026 Guidance");
  await page.screenshot({ path: test.info().outputPath("earnings-table.png"), fullPage: true });
});

test("API keys: a GitHub account makes a key, sees it once, and revokes it after a confirm", async ({ page }) => {
  const fake = await mockApi(page);
  await page.addInitScript((session) => localStorage.setItem("tributary.session", JSON.stringify(session)), GITHUB_SESSION);
  await page.goto("/#/settings");
  const keys = page.locator("section.api-keys");
  await expect(keys.getByRole("heading", { name: "API keys" })).toBeVisible();
  await expect(keys).toContainText("You have no API keys yet.");
  await keys.getByPlaceholder("What it's for: a notebook, a nightly job").fill("Nightly job");
  await keys.getByRole("radio", { name: "Read and write" }).click();
  await keys.getByRole("radio", { name: "365 days" }).click();
  await keys.getByRole("button", { name: "Create key" }).click();
  const once = keys.locator(".api-key-once");
  await expect(once.locator("code.secret")).toHaveText(NEW_API_KEY);
  await expect(once).toContainText("you won't see it again");
  const row = keys.locator("table.api-key-list tbody tr").first();
  await expect(row.locator("td")).toHaveText(["Nightly job", "trb_e2ee…", "Read and write", /2026/, "Never", /2027/, "None", "Revoke"]);
  await once.getByRole("button", { name: "I've saved it" }).click();
  await expect(once).toHaveCount(0);
  expect(await page.content()).not.toContain(NEW_API_KEY);
  expect(await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }))).not.toContain(NEW_API_KEY.slice(8));
  await row.getByRole("button", { name: "Revoke the key Nightly job" }).click();
  const confirm = page.locator(".ws-confirm");
  await expect(confirm).toContainText("Revoke the key Nightly job?");
  await expect(confirm.getByRole("button", { name: "Keep" })).toBeFocused();
  await confirm.getByRole("button", { name: "Revoke" }).click();
  await expect(row).toHaveClass("off");
  await expect(row).toContainText("Revoked");
  expect(fake.calls.filter((c) => c.startsWith("DELETE /api/query/v1/api-keys/"))).toHaveLength(1);
});

test("Connect an app: the consent screen names the app and where it goes back to; Approve follows the API's redirect", async ({ page }) => {
  const fake = await mockApi(page);
  const back: string[] = [];
  // the app's loopback redirect: answered here, so nothing outside the test is called
  await page.route(`${CONNECT_REDIRECT}**`, (route) => {
    back.push(route.request().url());
    return route.fulfill({ contentType: "text/html", body: "<p>Back in the app</p>" });
  });
  await page.addInitScript((session) => localStorage.setItem("tributary.session", JSON.stringify(session)), GITHUB_SESSION);
  const sealed = "eyJjIjoidHJjX0FBQUEifQ." + "s".repeat(43);
  await page.goto(`/#/connect?request=${sealed}`);
  const sheet = page.locator(".connect-sheet");
  await expect(sheet.getByRole("heading", { level: 1 })).toHaveText("Connect Claude to Tributary");
  await expect(sheet.locator(".connect-host")).toHaveText("127.0.0.1");
  await expect(sheet.locator(".connect-warn")).toContainText("An app on this computer");
  await expect(sheet).toContainText("read-only: no changes, no portfolio, no account settings");
  await expect(page.locator(".sidebar")).toHaveCount(0);
  await sheet.getByRole("button", { name: "Approve" }).click();
  await expect(page.locator("body")).toHaveText("Back in the app");
  expect(back).toHaveLength(1);
  expect(new URL(back[0]!).searchParams.get("code")).toBe("e2e-code");
  expect(fake.calls.filter((c) => c.startsWith("POST /api/query/v1/oauth/consent"))).toEqual([
    "POST /api/query/v1/oauth/consent", "POST /api/query/v1/oauth/consent/approve"]);
});

test("Connected apps: Settings lists an app and disconnects it after a confirm", async ({ page }) => {
  const fake = await mockApi(page);
  fake.connectedApps = [{ connection_id: "4b6f8a52-63f4-4d6e-9d0e-2f4d3b8a1c11", name: "Claude", kind: "metadata_document",
    publisher: "claude.ai", redirect_hosts: ["claude.ai"], connected_at: "2026-11-17T09:01:00Z", last_used_at: null }];
  await page.addInitScript((session) => localStorage.setItem("tributary.session", JSON.stringify(session)), GITHUB_SESSION);
  await page.goto("/#/settings");
  const apps = page.locator("section.connected-apps");
  await expect(apps.getByRole("heading", { name: "Connected apps" })).toBeVisible();
  const row = apps.locator("table.connected-app-list tbody tr").first();
  await expect(row.locator("td")).toHaveText(["Claudepublished by claude.ai", "claude.ai", /2026/, "Not yet", "Disconnect"]);
  await row.getByRole("button", { name: "Disconnect Claude" }).click();
  const confirm = page.locator(".ws-confirm");
  await expect(confirm).toContainText("Disconnect Claude?");
  await confirm.getByRole("button", { name: "Disconnect" }).click();
  await expect(apps).toContainText("No apps are connected.");
  expect(fake.calls.filter((c) => c.startsWith("DELETE /api/query/v1/oauth/connections/"))).toHaveLength(1);
});

test("API keys: a demo sandbox's Settings has none to offer", async ({ page }) => {
  await mockApi(page);
  await startDemo(page);
  await page.goto("/#/settings");
  await expect(page.getByRole("heading", { name: "Filing alerts" })).toBeVisible();
  await expect(page.locator("section.api-keys")).toHaveCount(0);
  await expect(page.locator("section.connected-apps")).toHaveCount(0);
});

test("Using the API is public: the front door links it, and its routes come from the API", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.getByRole("link", { name: "Using the API" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Using the API" })).toBeVisible();
  await expect(page).toHaveTitle("Using the API · Tributary");
  const rows = page.locator("table.api-routes tbody tr");
  await expect(rows).toHaveCount(9);
  await expect(rows.first()).toContainText("GET /v1/companies/search");
  await expect(page.locator(".api-code").first()).toContainText(`Authorization: Bearer $TRIBUTARY_API_KEY`);
  await expect(page.getByText("60 requests a minute and 10,000 a day")).toBeVisible();
  await page.locator(".status-home").click();
  await expect(page.getByRole("button", { name: "Try the demo" })).toBeVisible();
});

test("your data: a CSV checked, then stored, and its rows on the company page", async ({ page }) => {
  const fake = await mockApi(page);
  await startDemo(page);
  await page.goto("/#/data");
  await expect(page.locator(".folio").first()).toContainText("No datasets yet");
  await page.locator('input[type="file"]').setInputFiles({
    name: "Store counts.csv", mimeType: "text/csv",
    buffer: Buffer.from("Ticker,Date,Stores\nNVDA,2026-07-26,12\nNOPE,2026-07-26,1\n"),
  });
  await expect(page.locator("form.data-form select").first()).toHaveValue("Ticker");
  await page.getByRole("button", { name: "Check it" }).click();
  await expect(page.locator(".data-check")).toContainText("1 row for 1 company");
  await expect(page.locator(".data-check")).toContainText("(row 3)");
  expect(fake.datasets).toEqual([]);
  await page.getByRole("button", { name: "Store 1 row" }).click();
  await expect(page.locator(".data-table tbody tr")).toHaveCount(1);
  await expect(page.locator(".data-table tbody tr")).toContainText("mine.store_counts");
  await page.goto(`/#/company/${NVDA}`);
  await expect(page.locator(".your-data")).toContainText("Your own figures on");
  await expect(page.locator(".your-data tbody td").nth(1)).toHaveText("12");

  // a metric over it, saved into "Your metrics", then a screener column
  await page.goto("/#/data");
  await page.getByPlaceholder("e.g. Revenue per store").fill("Revenue per store");
  await page.locator("form.data-metric-form input.field").nth(1).fill("revenue / mine.store_counts.stores");
  await page.getByRole("button", { name: "Save metric" }).click();
  await expect(page.locator(".data-table").nth(1)).toContainText("revenue / mine.store_counts.stores");
  await page.getByRole("link", { name: "Screen with it" }).click();
  await expect(page).toHaveURL(/#\/companies\?metric=/);
  await expect(page.locator("table.screen thead")).toContainText("Revenue per store");
  expect(fake.calls.some((c) => c.startsWith("GET /api/query/v1/screener?") && c.includes("metric="))).toBe(true);
});

test("portfolio: a Fidelity export previewed then added, a holding by hand, and the journal's first entries", async ({ page }) => {
  const fake = await mockApi(page);
  await startDemo(page);
  await page.goto("/#/portfolio");
  await expect(page.locator(".portfolio-empty")).toContainText("No holdings yet. Track what you own against the research behind it");

  // the import: a preview first, nothing saved until Add; a missing cost is typed
  await page.getByRole("link", { name: "Import from Fidelity" }).click();
  await page.locator('input[type="file"]').setInputFiles({
    name: "Portfolio_Positions_Oct-21-2026.csv", mimeType: "text/csv",
    buffer: Buffer.from("Account Number,Account Name,Symbol,Quantity,Cost Basis Total\nZ09876543,Individual,NVDA,1.25,$200.00\n"),
  });
  const rows = page.locator(".import-table tbody tr");
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(2)).toContainText("Not covered: VANGUARD S&P 500 ETF");
  expect(fake.calls.filter((c) => c === "POST /api/query/v1/portfolio/import")).toEqual([]);
  const add = page.locator(".import-actions .btn.primary");
  await expect(add).toBeDisabled();
  await page.getByLabel("Cost of MSFT").fill("98.40");
  await expect(add).toHaveText("Add 3 positions");
  await add.click();
  await expect(page).toHaveURL(/#\/portfolio$/);
  const table = page.locator(".portfolio-table tbody tr");
  await expect(table).toHaveCount(3);
  await expect(table.first().locator("td").nth(3)).toHaveAttribute("title", "Your Fidelity export of Oct 21, 2026");
  await expect(page.locator(".not-advice")).toContainText("Not investment advice");

  // a holding by hand, with why it was bought: the journal's first entry
  await page.getByRole("button", { name: "Add a holding" }).click();
  await page.getByRole("radio", { name: "A fund or ETF" }).click();
  await page.getByPlaceholder("VOO").fill("SCHD");
  await page.getByPlaceholder("e.g. 10").fill("3");
  await page.getByPlaceholder("e.g. 1,250.00").fill("81.00");
  await page.getByPlaceholder("What you expect, and why now").fill("Dividend income while I learn.");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(table).toHaveCount(4);
  expect(fake.journal.map((e) => [e.symbol, e.kind])).toEqual([["SCHD", "bought"]]);

  // a holding's page: write a journal entry about the company
  await table.first().locator("td.first a").click();
  await page.getByRole("button", { name: "Write an entry" }).click();
  await page.getByPlaceholder("Your reasons, in your words").fill("Data-centre demand outruns supply.");
  await page.getByPlaceholder(/Margins below 50%/).fill("Gross margin under 60% two quarters running");
  await page.getByRole("button", { name: "Add to the journal" }).click();
  await expect(page.locator(".journal-item")).toHaveCount(1);
  await expect(page.locator(".journal-item .entry-why")).toHaveText("Data-centre demand outruns supply.");
  await expect(page.locator(".journal-item")).toContainText("Gross margin under 60% two quarters running");

  // the company page says it quietly
  await page.goto(`/#/company/${NVDA}`);
  await expect(page.locator(".co-held")).toContainText("You hold 1.25 shares");
});

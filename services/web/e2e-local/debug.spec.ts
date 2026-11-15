import { test } from "@playwright/test";
import { mockApi, startDemo } from "../e2e/mockApi";

test("debug", async ({ page }) => {
  page.on("response", (r) => {
    if (r.url().includes("/api/")) console.log(r.status(), new URL(r.url()).pathname + new URL(r.url()).search);
  });
  await mockApi(page);
  await startDemo(page);
  await page.goto(`/${process.env.HASH ?? "#/"}`);
  await page.waitForLoadState("networkidle");
});

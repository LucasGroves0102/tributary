import { test } from "@playwright/test";
import { mockApi, startDemo } from "../e2e/mockApi";

test("debug", async ({ page }) => {
  await mockApi(page);
  await startDemo(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/${process.env.HASH ?? "#/"}`);
  await page.waitForLoadState("networkidle");
  const wide = await page.evaluate(() => {
    const w = document.documentElement.clientWidth;
    return [...document.querySelectorAll("body *")]
      .map((e) => ({ e, r: e.getBoundingClientRect() }))
      .filter(({ r }) => r.right > w + 0.5)
      .slice(0, 12)
      .map(({ e, r }) => `${e.tagName}.${(e as HTMLElement).className} right=${r.right.toFixed(1)} w=${r.width.toFixed(1)}`);
  });
  console.log(JSON.stringify(wide, null, 1));
});

import { test, expect } from "@playwright/test";
import memorialFixture from "./fixtures/memorial.json";

test("renders the memorial map with the death count", async ({ page }) => {
  await page.route("**/api/memorial", (route) =>
    route.fulfill({ json: memorialFixture })
  );

  await page.goto("/");

  // Map container is present
  await expect(page.locator(".maplibregl-canvas")).toBeVisible({ timeout: 10_000 });

  // Title and count chrome render
  await expect(page.getByText("Crime Pipeline — Memorial")).toBeVisible();
  await expect(page.getByText("3", { exact: false })).toBeVisible();
  await expect(page.getByText("names")).toBeVisible();
});

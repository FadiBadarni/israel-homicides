import { test, expect } from "@playwright/test";
import memorialFixture from "./fixtures/memorial.json";

test("renders the memorial map with the death count", async ({ page }) => {
  await page.route("**/api/memorial", (route) =>
    route.fulfill({ json: memorialFixture })
  );

  await page.goto("/");

  await expect(page.getByTestId("israel-map")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("Crime Pipeline — Memorial")).toBeVisible();

  const deathCount = page.getByTestId("death-count");
  await expect(deathCount).toBeVisible();
  await expect(deathCount).toContainText("3");
  await expect(deathCount).toContainText("names");
});

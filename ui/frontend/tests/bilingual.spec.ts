import { test, expect } from "@playwright/test";
import memorialFixture from "./fixtures/memorial.json";

test("language toggle switches the UI from Arabic to Hebrew", async ({ page }) => {
  await page.route("**/api/memorial", (route) =>
    route.fulfill({ json: memorialFixture })
  );

  await page.goto("/");

  // Default Arabic
  await expect(page.getByText("القضايا الأحدث")).toBeVisible({ timeout: 10_000 });

  // Click Hebrew toggle
  await page.getByRole("button", { name: "עברית" }).click();

  // Hebrew label appears
  await expect(page.getByText("תיקים אחרונים")).toBeVisible();
  // Arabic section label gone
  await expect(page.getByText("القضايا الأحدث")).toBeHidden();

  // html lang attribute flipped
  const lang = await page.locator("html").getAttribute("lang");
  expect(lang).toBe("he");
});

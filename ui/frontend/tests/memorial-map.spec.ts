import { test, expect } from "@playwright/test";
import memorialFixture from "./fixtures/memorial.json";

test("renders the memorial home page with hero and recent cases", async ({ page }) => {
  await page.route("**/api/memorial", (route) =>
    route.fulfill({ json: memorialFixture })
  );

  await page.goto("/");

  // Hero
  await expect(page.getByText("كلّ ضحيّة لها اسم.")).toBeVisible({ timeout: 10_000 });

  // Cases section heading
  await expect(page.getByText("القضايا الأحدث")).toBeVisible();

  // At least one victim name from the fixture
  await expect(page.getByText("أليس")).toBeVisible();
});

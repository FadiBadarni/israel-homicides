import { test, expect } from "@playwright/test";
import memorialFixture from "./fixtures/memorial.json";

test("bloom card opens, lists victims, swaps to case detail, closes on ESC", async ({ page }) => {
  await page.route("**/api/memorial", (route) =>
    route.fulfill({ json: memorialFixture })
  );
  await page.route("**/api/cases/**", (route) =>
    route.fulfill({
      json: {
        case_index: 0,
        run_id: "test-run",
        victim_name: "Alice",
        victim_name_he: "אליס",
        victim_name_ar: "أليس",
        victim_name_en: "Alice",
        victim_age: 24,
        victim_gender: "female",
        incident_date: "2026-04-19",
        death_date: "2026-04-19",
        city: "Tira",
        neighborhood: null,
        district: null,
        weapon_type: "firearm",
        suspect_status: null,
        legal_status: null,
        case_narrative: "A test narrative.",
        sources: [],
        media_evidence: [],
        conflict_map: null,
      },
    })
  );

  await page.goto("/?locality=tira");
  await expect(page.getByTestId("israel-map")).toBeVisible({ timeout: 10_000 });

  // BidiName prefers Hebrew when present; Alice's fixture has victim_name_he set.
  await expect(page.getByText("אליס")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("Bob")).toBeVisible();

  await page.getByText("אליס").click();
  await expect(page.getByText("A test narrative.")).toBeVisible({ timeout: 10_000 });

  await page.keyboard.press("Escape");
  await expect(page.getByText("A test narrative.")).toBeHidden();
});

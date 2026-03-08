import { test, expect } from "@playwright/test";

/**
 * Smoke test: Page loads and shows the Kanban board layout.
 */
test.describe("Page Load", () => {
  test("loads the dashboard with Kanban columns", async ({ page }) => {
    await page.goto("/");

    // Header should be visible
    await expect(
      page.locator("h1", { hasText: "HelixOS Dashboard" }),
    ).toBeVisible();

    // All 5 Kanban columns should render
    const columns = ["BACKLOG", "REVIEW", "QUEUED", "RUNNING", "DONE"];
    for (const col of columns) {
      await expect(
        page.locator("h2", { hasText: col }),
      ).toBeVisible();
    }
  });

  test("shows connection status indicator", async ({ page }) => {
    await page.goto("/");

    // Should show either Connected or Disconnected status
    const status = page.locator("text=Connected").or(
      page.locator("text=Disconnected"),
    );
    await expect(status.first()).toBeVisible();
  });

  test("header buttons are present", async ({ page }) => {
    await page.goto("/");

    await expect(
      page.locator("button", { hasText: "Import Project" }),
    ).toBeVisible();
    await expect(
      page.locator("button", { hasText: /Sync All/ }),
    ).toBeVisible();
  });
});

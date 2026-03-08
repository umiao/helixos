import { test, expect } from "@playwright/test";

/**
 * Smoke test: Task cards render in the Kanban board.
 *
 * Requires at least one task to exist in the backend.
 * If no tasks exist, the test verifies the empty-state UI instead.
 */
test.describe("Task Card Rendering", () => {
  test("task cards or empty state are visible", async ({ page }) => {
    await page.goto("/");

    // Wait for loading to finish (skeleton cards disappear)
    // Skeleton cards have animate-pulse class; wait for them to vanish
    await page.waitForTimeout(2000);

    // Either task cards (rounded-lg border bg-white) or "No tasks" placeholder
    const taskCards = page.locator(
      ".rounded-lg.border.bg-white.p-3.shadow-sm",
    );
    const emptyState = page.locator("text=No tasks");

    const cardCount = await taskCards.count();
    const emptyCount = await emptyState.count();

    // At least one must be true: cards exist OR empty state is shown
    expect(cardCount > 0 || emptyCount > 0).toBeTruthy();
  });

  test("task card shows task ID and title", async ({ page }) => {
    await page.goto("/");
    await page.waitForTimeout(2000);

    const taskCards = page.locator(
      ".rounded-lg.border.bg-white.p-3.shadow-sm",
    );
    const count = await taskCards.count();

    if (count > 0) {
      const firstCard = taskCards.first();

      // Task ID is in a font-mono span
      const taskId = firstCard.locator(".font-mono.font-semibold");
      await expect(taskId).toBeVisible();

      // Title is in a text-sm font-medium paragraph
      const title = firstCard.locator("p.text-sm.font-medium");
      await expect(title).toBeVisible();
      const titleText = await title.textContent();
      expect(titleText?.length).toBeGreaterThan(0);
    }
    // Skip assertion if no tasks (verified in previous test)
  });

  test("task card shows status badge", async ({ page }) => {
    await page.goto("/");
    await page.waitForTimeout(2000);

    const taskCards = page.locator(
      ".rounded-lg.border.bg-white.p-3.shadow-sm",
    );
    const count = await taskCards.count();

    if (count > 0) {
      const firstCard = taskCards.first();

      // Status badge is a rounded-full span with status text
      const validStatuses = [
        "BACKLOG",
        "REVIEW",
        "AUTO-APPROVED",
        "NEEDS HUMAN",
        "QUEUED",
        "RUNNING",
        "DONE",
        "FAILED",
        "BLOCKED",
      ];
      const badge = firstCard.locator(".rounded-full.font-semibold").first();
      await expect(badge).toBeVisible();
      const badgeText = await badge.textContent();
      const matchesStatus = validStatuses.some(
        (s) => badgeText?.includes(s),
      );
      expect(matchesStatus).toBeTruthy();
    }
  });
});

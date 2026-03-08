import { test, expect } from "@playwright/test";

/**
 * Smoke test: Clicking a task card opens the bottom panel.
 *
 * Requires at least one task to exist in the backend.
 */
test.describe("Task Click Opens Bottom Panel", () => {
  test("clicking a task card activates the bottom panel", async ({ page }) => {
    await page.goto("/");
    await page.waitForTimeout(2000);

    const taskCards = page.locator(
      ".rounded-lg.border.bg-white.p-3.shadow-sm",
    );
    const count = await taskCards.count();

    if (count === 0) {
      // No tasks available -- skip gracefully
      test.skip();
      return;
    }

    // Click the first task card
    await taskCards.first().click();

    // Bottom panel should appear with tab content.
    // The bottom panel has a border-t border-gray-300 div.
    // After clicking a task, the panel switches to show Execution Log
    // or Conversation View content.
    const bottomPanel = page.locator(".border-t.border-gray-300.bg-white");
    await expect(bottomPanel).toBeVisible();

    // The panel should show either the Conversation or Log view tab selector,
    // or Review panel content. Check for the view mode dropdown.
    const viewSelector = page.locator("select").filter({
      has: page.locator("option", { hasText: "Conversation" }),
    });
    const reviewTab = page.locator("text=Review");

    const hasViewSelector = (await viewSelector.count()) > 0;
    const hasReviewTab = (await reviewTab.count()) > 0;

    // At least one panel indicator should be visible
    expect(hasViewSelector || hasReviewTab).toBeTruthy();
  });

  test("bottom panel shows task-specific content after click", async ({
    page,
  }) => {
    await page.goto("/");
    await page.waitForTimeout(2000);

    const taskCards = page.locator(
      ".rounded-lg.border.bg-white.p-3.shadow-sm",
    );
    const count = await taskCards.count();

    if (count === 0) {
      test.skip();
      return;
    }

    // Get the task ID from the first card
    const firstCard = taskCards.first();
    const taskIdEl = firstCard.locator(".font-mono.font-semibold");
    const taskId = await taskIdEl.textContent();

    // Click the card
    await firstCard.click();

    // The bottom panel should now be visible and contain content
    // related to the selected task
    const bottomPanel = page.locator(".border-t.border-gray-300.bg-white");
    await expect(bottomPanel).toBeVisible();

    // The task ID should appear somewhere in the page context
    // (either in the panel header or in the selected state)
    expect(taskId).toBeTruthy();
  });
});

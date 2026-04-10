import { test, expect } from "@playwright/test";

test("console primary navigation (overview/governance/ledger/agents)", async ({ page }) => {
  await page.goto("/console/");
  await expect(page.getByRole("main").getByRole("heading", { name: "Overview" })).toBeVisible();

  await page.getByRole("link", { name: "Governance" }).click();
  await expect(page.getByRole("main").getByRole("heading", { name: "Governance" })).toBeVisible();

  await page.getByRole("link", { name: "Ledger" }).click();
  await expect(page.getByRole("main").getByRole("heading", { name: "Ledger", exact: true })).toBeVisible();

  await page.getByRole("link", { name: "Agents" }).click();
  await expect(page.getByRole("main").getByRole("heading", { name: "Agents", exact: true })).toBeVisible();
});

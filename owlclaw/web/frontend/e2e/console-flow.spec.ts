/// <reference types="node" />
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";


/** Assert overview API response matches contract: health_checks[].component, healthy */
function assertOverviewContract(json: unknown): void {
  const data = json as Record<string, unknown>;
  expect(Array.isArray(data.health_checks)).toBe(true);
  const checks = data.health_checks as Array<{ component?: string; healthy?: boolean }>;
  expect(checks.length).toBeGreaterThan(0);
  for (const c of checks) {
    expect(typeof c.component).toBe("string");
    expect(typeof c.healthy).toBe("boolean");
  }
}

test.describe("Console flow", () => {
  test("Overview -> Governance -> Ledger navigation", async ({ page }) => {
    await page.goto(`/console/`);
    await expect(page.getByRole("main").getByRole("heading", { name: "Overview" })).toBeVisible();

    await page.getByRole("link", { name: "Governance" }).click();
    await expect(page.getByRole("main").getByRole("heading", { name: "Governance" })).toBeVisible();

    await page.getByRole("link", { name: "Ledger" }).click();
    await expect(page.getByRole("main").getByRole("heading", { name: "Ledger", exact: true })).toBeVisible();
  });

  test("Overview -> Agents navigation and empty state", async ({ page }) => {
    await page.goto(`/console/`);
    await expect(page.getByRole("main").getByRole("heading", { name: "Overview" })).toBeVisible();

    await page.getByRole("link", { name: "Agents" }).click();
    await expect(page.getByRole("main").getByRole("heading", { name: "Agents", exact: true })).toBeVisible();
    // No-DB: expect friendly empty state (EmptyState "No agents found"), not 500/error
    await expect(page.getByText("No agents found")).toBeVisible({ timeout: 5000 });
  });

  test("Governance page triggers governance API calls", async ({ page }) => {
    const apiCalls: string[] = [];
    page.on("request", (req) => {
      if (req.url().includes("/api/v1/")) apiCalls.push(req.url());
    });

    await page.goto(`/console/`);
    await page.getByRole("link", { name: "Governance" }).click();
    await expect(page.getByRole("main").getByRole("heading", { name: "Governance" })).toBeVisible();
    // Wait for loading to complete (day/week/month buttons or error) so API calls have fired
    await Promise.race([
      page.getByRole("button", { name: /day/i }).waitFor({ state: "visible", timeout: 8000 }),
      page.getByText(/Failed to load/).waitFor({ state: "visible", timeout: 8000 }),
    ]).catch(() => null);
    await page.waitForTimeout(300);

    const govCalls = apiCalls.filter((u) => /governance\/(budget|circuit-breakers|visibility-matrix)/.test(u));
    expect(govCalls.length).toBeGreaterThanOrEqual(1);
  });

  test("Governance granularity switch triggers new API request (F-7)", async ({ page }) => {
    const apiCalls: string[] = [];
    page.on("request", (req) => {
      if (req.url().includes("/api/v1/")) apiCalls.push(req.url());
    });

    await page.goto(`/console/`);
    await page.getByRole("link", { name: "Governance" }).click();
    await expect(page.getByRole("main").getByRole("heading", { name: "Governance" })).toBeVisible();
    await page.getByRole("button", { name: /day/i }).waitFor({ state: "visible", timeout: 8000 });
    const beforeClick = apiCalls.filter((u) => u.includes("granularity=week")).length;

    await page.getByRole("button", { name: "week" }).click();
    await page.waitForTimeout(500);

    const afterClick = apiCalls.filter((u) => u.includes("granularity=week"));
    expect(afterClick.length).toBeGreaterThan(beforeClick);
  });

  test("Ledger Apply filter triggers new API request with params (F-11)", async ({ page }) => {
    const apiCalls: string[] = [];
    page.on("request", (req) => {
      if (req.url().includes("/api/v1/ledger")) apiCalls.push(req.url());
    });

    await page.goto(`/console/`);
    await page.getByRole("link", { name: "Ledger" }).click();
    await expect(page.getByRole("main").getByRole("heading", { name: "Ledger", exact: true })).toBeVisible();
    await expect(page.getByRole("main").getByRole("heading", { name: "Filters" })).toBeVisible({ timeout: 5000 });
    await page.getByPlaceholder("Agent").fill("test-agent-id");
    const beforeApply = apiCalls.length;

    await page.getByRole("button", { name: "Apply" }).click();
    await page.waitForTimeout(500);

    const ledgerCalls = apiCalls.filter((u) => u.includes("agent_id=") || u.includes("agent_id%3D"));
    expect(ledgerCalls.length).toBeGreaterThanOrEqual(1);
  });

  test("Ledger sort change triggers order_by request param (F-14)", async ({ page }) => {
    const apiCalls: string[] = [];
    page.on("request", (req) => {
      if (req.url().includes("/api/v1/ledger")) apiCalls.push(req.url());
    });

    await page.route("**/api/v1/ledger*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [], total: 0, limit: 20, offset: 0 }),
      });
    });

    await page.goto(`/console/`);
    await page.getByRole("link", { name: "Ledger" }).click();
    await expect(page.getByRole("main").getByRole("heading", { name: "Ledger", exact: true })).toBeVisible();
    await page.getByLabel("Order By").selectOption("cost_desc");
    await page.getByRole("button", { name: "Apply" }).click();
    await page.waitForTimeout(500);

    const sortedCalls = apiCalls.filter((u) => u.includes("order_by=cost_desc") || u.includes("order_by%3Dcost_desc"));
    expect(sortedCalls.length).toBeGreaterThanOrEqual(1);
  });

  test("Ledger filter panel and empty state", async ({ page }) => {
    await page.goto(`/console/`);
    await page.getByRole("link", { name: "Ledger" }).click();
    await expect(page.getByRole("main").getByRole("heading", { name: "Ledger", exact: true })).toBeVisible();
    await expect(page.getByRole("main").getByRole("heading", { name: "Filters" })).toBeVisible({ timeout: 5000 });
    await expect(page.getByPlaceholder("Agent")).toBeVisible();
    await expect(page.getByRole("heading", { name: "No ledger records" }).or(page.getByRole("button", { name: "Reset Filters" })).first()).toBeVisible({ timeout: 3000 });
  });

  test("First load under 5s", async ({ page }) => {
    const start = Date.now();
    await page.goto(`/console/`);
    await expect(page.getByRole("main").getByRole("heading", { name: "Overview" })).toBeVisible();
    const elapsed = Date.now() - start;
    expect(elapsed).toBeLessThan(5000);
  });

  test("Capabilities, Triggers, and Settings pages load", async ({ page }) => {
    await page.goto(`/console/`);
    await page.getByRole("link", { name: "Capabilities" }).click();
    await expect(page.getByRole("main").getByRole("heading", { name: "Capabilities" })).toBeVisible();

    await page.getByRole("link", { name: "Triggers" }).click();
    await expect(page.getByRole("main").getByRole("heading", { name: "Triggers", exact: true })).toBeVisible();
    await page.waitForTimeout(2000);
    // No-DB: loading, error, empty state, or list — not white screen
    const content = page.getByRole("main");
    await expect(content).toContainText(/Failed to load|No triggers|Trigger List|Unified runtime|Loading triggers/);

    await page.getByRole("link", { name: "Settings" }).click();
    await expect(page.getByRole("main").getByRole("heading", { name: "Settings" })).toBeVisible();
  });

  test("Agents detail panel renders identity/memory/knowledge/history with mock data (F-16)", async ({ page }) => {
    await page.route("**/api/v1/agents", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            {
              id: "agent-1",
              name: "Agent One",
              role: "trading",
              status: "active",
              identity_summary: "Primary trading agent",
            },
          ],
        }),
      });
    });
    await page.route("**/api/v1/agents/agent-1", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "agent-1",
          name: "Agent One",
          role: "trading",
          status: "active",
          identity: { soul: "disciplined", timezone: "Asia/Shanghai" },
          memory: { short_term_count: 3, long_term_count: 7 },
          knowledge: { skills: ["entry-monitor"], references_count: 2 },
          recent_runs: [{ timestamp: "2026-03-05T10:00:00Z", capability: "entry-monitor", status: "success", cost_usd: 0.02 }],
        }),
      });
    });

    await page.goto(`/console/agents`);
    await expect(page.getByRole("main").getByRole("heading", { name: "Agents", exact: true })).toBeVisible();
    await page.getByRole("button", { name: /Agent One/i }).click();
    await expect(page.getByText("Identity Config")).toBeVisible();
    await expect(page.getByText("STM Records")).toBeVisible();
    await expect(page.getByText("LTM Records")).toBeVisible();
    await expect(page.getByText("Knowledge")).toBeVisible();
    await expect(page.getByText("Recent Runs")).toBeVisible();
  });

  test("Triggers list renders with mock trigger records (F-18)", async ({ page }) => {
    await page.route("**/api/v1/triggers*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            {
              id: "t-1",
              type: "cron",
              name: "daily-budget-check",
              next_run: "2026-03-06T00:00:00Z",
              last_status: "success",
            },
          ],
        }),
      });
    });

    await page.goto(`/console/triggers`);
    await expect(page.getByRole("main").getByRole("heading", { name: "Triggers", exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Trigger List" })).toBeVisible();
    await expect(page.getByText("daily-budget-check")).toBeVisible();
    await expect(page.getByText("Next run: 2026-03-06T00:00:00Z")).toBeVisible();
  });

  test("Tab key traverses sidebar", async ({ page }) => {
    await page.goto(`/console/`);
    await page.keyboard.press("Tab");
    await page.keyboard.press("Tab");
    const focused = await page.evaluate(() => document.activeElement?.tagName);
    expect(["A", "BUTTON", "DIV"]).toContain(focused);
  });

  // --- Deep tests: Overview ---
  test("Overview has System Health and component checks (F-1)", async ({ page }) => {
    await page.goto(`/console/`);
    await expect(page.getByRole("main").getByRole("heading", { name: "Overview" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "System Health" })).toBeVisible({ timeout: 5000 });
    // At least one component (runtime, db, hatchet, llm, etc.)
    const healthItems = page.locator('li:has-text("OK"), li:has-text("Down")');
    await expect(healthItems.first()).toBeVisible({ timeout: 3000 });
  });

  test("Overview has First Run Guide with Quick Start link (F-5)", async ({ page }) => {
    await page.goto(`/console/`);
    await expect(page.getByRole("heading", { name: "First Run Guide" })).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole("link", { name: "Quick Start" })).toBeVisible();
    await expect(page.getByRole("link", { name: "SKILL.md Guide" })).toBeVisible();
  });

  test("Overview attempts WebSocket connection (N-7)", async ({ page }) => {
    const wsUrls: string[] = [];
    page.on("websocket", (ws) => {
      wsUrls.push(ws.url());
    });
    await page.goto(`/console/`);
    await expect(page.getByRole("main").getByRole("heading", { name: "Overview" })).toBeVisible();
    await page.waitForTimeout(1500);
    const apiWs = wsUrls.filter((u) => u.includes("/api/v1/ws"));
    expect(apiWs.length).toBeGreaterThanOrEqual(1);
  });

  // --- Deep tests: Governance ---
  test("Governance has Circuit Breakers section (F-8)", async ({ page }) => {
    await page.goto(`/console/`);
    await page.getByRole("link", { name: "Governance" }).click();
    await expect(page.getByRole("main").getByRole("heading", { name: "Governance" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Circuit Breakers" })).toBeVisible({ timeout: 5000 });
  });

  test("Governance has Capability Visibility Matrix (F-9)", async ({ page }) => {
    await page.goto(`/console/`);
    await page.getByRole("link", { name: "Governance" }).click();
    await expect(page.getByRole("heading", { name: "Capability Visibility Matrix" })).toBeVisible({ timeout: 5000 });
  });

  // --- Deep tests: Ledger with mocked data ---
  test("Ledger with mock data: Table/Timeline toggle and record detail (F-10, F-12)", async ({ page }) => {
    const mockLedger = {
      items: [
        {
          id: "rec-1",
          timestamp: "2026-03-04T12:00:00Z",
          agent: "agent-a",
          capability: "cap-x",
          status: "success",
          cost_usd: 0.01,
          model: "gpt-4",
          latency_ms: 100,
          input: "in",
          output: "out",
          reasoning: "reason",
        },
      ],
      total: 1,
      limit: 20,
      offset: 0,
    };

    await page.route("**/api/v1/ledger*", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mockLedger) });
    });

    await page.goto(`/console/`);
    await page.getByRole("link", { name: "Ledger" }).click();
    await expect(page.getByRole("main").getByRole("heading", { name: "Ledger", exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Execution Records" })).toBeVisible({ timeout: 5000 });
    // Table/Timeline toggle
    await expect(page.getByRole("button", { name: "Table" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Timeline" })).toBeVisible();
    // Click record -> detail panel
    await page.getByRole("cell", { name: "agent-a" }).or(page.getByText("agent-a")).first().click();
    await expect(page.getByRole("heading", { name: "Execution Detail" })).toBeVisible();
    await expect(page.getByText("Record: rec-1")).toBeVisible();
    await expect(page.getByText("Agent: agent-a")).toBeVisible();
  });

  test("Ledger with mock data: pagination triggers offset request (F-13)", async ({ page }) => {
    const mockLedgerPage1 = {
      items: Array.from({ length: 20 }, (_, i) => ({
        id: `rec-${i}`,
        timestamp: "2026-03-04T12:00:00Z",
        agent: "agent-a",
        capability: "cap-x",
        status: "success",
        cost_usd: 0.01,
        model: "gpt-4",
        latency_ms: 100,
        input: "",
        output: "",
        reasoning: "",
      })),
      total: 25,
      limit: 20,
      offset: 0,
    };

    const ledgerUrls: string[] = [];
    await page.route("**/api/v1/ledger*", async (route) => {
      ledgerUrls.push(route.request().url());
      const offset = new URL(route.request().url()).searchParams.get("offset") || "0";
      const body =
        offset === "0"
          ? mockLedgerPage1
          : {
              items: mockLedgerPage1.items.slice(0, 5),
              total: 25,
              limit: 20,
              offset: 20,
            };
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    });

    await page.goto(`/console/`);
    await page.getByRole("link", { name: "Ledger" }).click();
    await expect(page.getByRole("heading", { name: "Execution Records" })).toBeVisible({ timeout: 5000 });
    const beforeNext = ledgerUrls.length;
    await page.getByRole("button", { name: "Next" }).click();
    await page.waitForTimeout(400);
    const offset20 = ledgerUrls.filter((u) => u.includes("offset=20") || u.includes("offset%3D20"));
    expect(offset20.length).toBeGreaterThan(0);
  });

  // --- Deep tests: Network / no unexpected errors ---
  test("Overview and main nav: no unexpected 4xx/5xx on API calls", async ({ page }) => {
    const failed: { url: string; status: number }[] = [];
    page.on("response", (res) => {
      const u = res.url();
      if (u.includes("/api/v1/") && res.status() >= 400) {
        failed.push({ url: u, status: res.status() });
      }
    });

    await page.goto(`/console/`);
    await expect(page.getByRole("main").getByRole("heading", { name: "Overview" })).toBeVisible();
    await page.getByRole("link", { name: "Governance" }).click();
    await expect(page.getByRole("main").getByRole("heading", { name: "Governance" })).toBeVisible();
    await page.getByRole("link", { name: "Ledger" }).click();
    await expect(page.getByRole("main").getByRole("heading", { name: "Ledger", exact: true })).toBeVisible();
    await page.getByRole("link", { name: "Agents" }).click();
    await expect(page.getByRole("main").getByRole("heading", { name: "Agents", exact: true })).toBeVisible();
    await page.waitForTimeout(500);

    // Known acceptable failures: agents/{id}, triggers (500 when no DB); ws (404 when uvicorn has no websockets)
    const unexpected = failed.filter(
      (f) =>
        !f.url.match(/\/agents\/[^/]+\/?$/) &&
        !f.url.includes("/triggers") &&
        !f.url.includes("/api/v1/ws")
    );
    expect(unexpected).toEqual([]);
  });

  // --- Deep tests: Settings structure ---
  test("Settings shows runtime, database, version sections", async ({ page }) => {
    await page.goto(`/console/`);
    await page.getByRole("link", { name: "Settings" }).click();
    await expect(page.getByRole("main").getByRole("heading", { name: "Settings" })).toBeVisible();
    // Settings page has key sections
    await expect(page.getByText(/runtime|database|version/i).first()).toBeVisible({ timeout: 5000 });
  });

  // --- Contract & negative path (depth) ---
  test("Overview API response matches contract: health_checks structure", async ({ page }) => {
    const respPromise = page.waitForResponse(
      (r) => r.url().includes("/api/v1/overview") && r.status() === 200,
      { timeout: 10000 }
    );
    await page.goto(`/console/`);
    const resp = await respPromise;
    const json = await resp.json();
    assertOverviewContract(json);
  });

  test("Negative: overview 500 returns friendly error, no white screen", async ({ page }) => {
    await page.route("**/api/v1/overview", (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ error: { code: "INTERNAL_ERROR", message: "Server error" } }),
      })
    );
    await page.goto(`/console/`);
    await page.waitForTimeout(2500);
    const main = page.getByRole("main");
    await expect(main).toBeVisible();
    await expect(page.getByRole("main").getByRole("heading", { name: "Overview" })).toBeVisible();
    await expect(page.getByText(/Failed to load overview metrics/i)).toBeVisible({ timeout: 5000 });
  });

  test("Negative: governance 500 returns friendly error", async ({ page }) => {
    const errBody = { status: 500, contentType: "application/json" as const, body: JSON.stringify({ error: { code: "INTERNAL_ERROR", message: "Governance unavailable" } }) };
    await page.context().route("**/api/v1/governance/budget*", (r) => r.fulfill(errBody));
    await page.context().route("**/api/v1/governance/circuit-breakers*", (r) => r.fulfill(errBody));
    await page.context().route("**/api/v1/governance/visibility-matrix*", (r) => r.fulfill(errBody));
    await page.goto(`/console/governance`);
    await expect(page.getByRole("main").getByRole("heading", { name: "Governance" })).toBeVisible();
    await expect(page.getByText(/Failed to load governance data/i)).toBeVisible({ timeout: 8000 });
  });

  test("Negative: ledger 422 returns friendly error (API-17 ErrorResponse structure)", async ({ page }) => {
    const errBody = { error: { code: "VALIDATION_ERROR", message: "Invalid filter parameters" } };
    await page.context().route("**/api/v1/ledger*", (route) =>
      route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify(errBody),
      })
    );
    await page.goto(`/console/ledger`);
    await expect(page.getByRole("main").getByRole("heading", { name: "Ledger", exact: true })).toBeVisible();
    await expect(page.getByText(/Failed to load ledger|Invalid filter parameters/i)).toBeVisible({ timeout: 8000 });
  });

  test("No uncaught JavaScript errors on Overview load", async ({ page }) => {
    const errors: Error[] = [];
    page.on("pageerror", (e) => errors.push(e));
    await page.goto(`/console/`);
    await expect(page.getByRole("main").getByRole("heading", { name: "Overview" })).toBeVisible();
    await page.waitForTimeout(500);
    expect(errors).toHaveLength(0);
  });

  test("No uncaught JavaScript errors across main nav (N-10)", async ({ page }) => {
    const errors: Error[] = [];
    page.on("pageerror", (e) => errors.push(e));
    await page.goto(`/console/`);
    await expect(page.getByRole("main").getByRole("heading", { name: "Overview" })).toBeVisible();
    await page.getByRole("link", { name: "Governance" }).click();
    await expect(page.getByRole("main").getByRole("heading", { name: "Governance" })).toBeVisible();
    await page.getByRole("link", { name: "Ledger" }).click();
    await expect(page.getByRole("main").getByRole("heading", { name: "Ledger", exact: true })).toBeVisible();
    await page.getByRole("link", { name: "Agents" }).click();
    await expect(page.getByRole("main").getByRole("heading", { name: "Agents", exact: true })).toBeVisible();
    await page.waitForTimeout(500);
    expect(errors).toHaveLength(0);
  });

  test("No sensitive info leak in console (no raw credentials in logs)", async ({ page }) => {
    const consoleLogs: string[] = [];
    page.on("console", (msg) => consoleLogs.push(msg.text()));
    await page.goto(`/console/`);
    await expect(page.getByRole("main").getByRole("heading", { name: "Overview" })).toBeVisible();
    await page.waitForTimeout(1000);
    // No log should contain JWT-like (eyJ...) or API key-like (sk-...) patterns
    const leaked = consoleLogs.some((t) => /eyJ[A-Za-z0-9_-]{20,}/.test(t) || /sk-[A-Za-z0-9]{20,}/.test(t));
    expect(leaked).toBe(false);
  });

  test("Accessibility: Overview has no WCAG A/AA violations (axe)", async ({ page }) => {
    await page.goto(`/console/`);
    await expect(page.getByRole("main").getByRole("heading", { name: "Overview" })).toBeVisible();
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(results.violations).toEqual([]);
  });

  test("Accessibility: Governance has no WCAG A/AA violations (axe)", async ({ page }) => {
    await page.goto(`/console/`);
    await page.getByRole("link", { name: "Governance" }).click();
    await expect(page.getByRole("main").getByRole("heading", { name: "Governance" })).toBeVisible();
    await page.getByRole("button", { name: /day/i }).waitFor({ state: "visible", timeout: 8000 }).catch(() => null);
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(results.violations).toEqual([]);
  });

  test("Accessibility: Ledger has no WCAG A/AA violations (axe)", async ({ page }) => {
    await page.goto(`/console/`);
    await page.getByRole("link", { name: "Ledger" }).click();
    await expect(page.getByRole("main").getByRole("heading", { name: "Ledger", exact: true })).toBeVisible();
    await expect(page.getByRole("main").getByRole("heading", { name: "Filters" })).toBeVisible({ timeout: 8000 });
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(results.violations).toEqual([]);
  });

  test("Governance API response matches contract: budget and circuit_breakers", async ({ page }) => {
    await page.goto(`/console/`);
    const budgetPromise = page.waitForResponse(
      (r) => r.url().includes("/api/v1/governance/budget") && r.status() === 200,
      { timeout: 10000 }
    );
    const circuitPromise = page.waitForResponse(
      (r) => r.url().includes("/api/v1/governance/circuit-breakers") && r.status() === 200,
      { timeout: 10000 }
    );
    await page.getByRole("link", { name: "Governance" }).click();
    const [budgetResp, circuitResp] = await Promise.all([budgetPromise, circuitPromise]);
    const budgetJson = (await budgetResp.json()) as Record<string, unknown>;
    expect(budgetJson).toHaveProperty("start_date");
    expect(budgetJson).toHaveProperty("end_date");
    expect(budgetJson).toHaveProperty("granularity");
    expect(Array.isArray(budgetJson.items)).toBe(true);
    const circuitJson = (await circuitResp.json()) as Record<string, unknown>;
    expect(Array.isArray(circuitJson.items)).toBe(true);
  });

  test("E-3: Navigation is SPA (no full reload between pages)", async ({ page }) => {
    const htmlRequests: string[] = [];
    page.on("request", (req) => {
      const u = req.url();
      if (u.endsWith("/console/") || u.endsWith("/console") || u.match(/\/console\/?$/)) htmlRequests.push(u);
    });
    await page.goto(`/console/`);
    await expect(page.getByRole("main").getByRole("heading", { name: "Overview" })).toBeVisible();
    await page.getByRole("link", { name: "Governance" }).click();
    await expect(page.getByRole("main").getByRole("heading", { name: "Governance" })).toBeVisible();
    await page.getByRole("link", { name: "Ledger" }).click();
    await expect(page.getByRole("main").getByRole("heading", { name: "Ledger", exact: true })).toBeVisible();
    // Only initial load should fetch HTML; SPA nav does not reload
    expect(htmlRequests.length).toBeLessThanOrEqual(1);
  });

  test("E-2: 1024px layout keeps sidebar and main content usable", async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 800 });
    await page.goto(`/console/`);
    await expect(page.getByRole("complementary")).toBeVisible();
    await expect(page.getByRole("main").getByRole("heading", { name: "Overview" })).toBeVisible();
    await page.getByRole("link", { name: "Ledger" }).click();
    await expect(page.getByRole("main").getByRole("heading", { name: "Ledger", exact: true })).toBeVisible();
  });

  test("F-20: Traces and Workflows pages expose external dashboard links", async ({ page }) => {
    await page.goto(`/console/traces`);
    await expect(page.getByRole("main").getByRole("heading", { name: "Traces", exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "Open Langfuse Dashboard" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Open Traces in New Tab" })).toBeVisible();

    await page.goto(`/console/workflows`);
    await expect(page.getByRole("main").getByRole("heading", { name: "Workflows", exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "Open Hatchet Dashboard" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Open Workflows" })).toBeVisible();
  });

  test("E-9: Overview color contrast has no violations (axe color-contrast rule)", async ({ page }) => {
    await page.goto(`/console/`);
    await expect(page.getByRole("main").getByRole("heading", { name: "Overview" })).toBeVisible();
    const results = await new AxeBuilder({ page }).withRules(["color-contrast"]).analyze();
    expect(results.violations).toEqual([]);
  });

  test("E-6: Governance with empty data shows chart/sections, no white screen", async ({ page }) => {
    await page.context().route("**/api/v1/governance/**", async (route) => {
      const url = route.request().url();
      const body = url.includes("budget")
        ? { start_date: "2026-03-01", end_date: "2026-03-04", granularity: "day", items: [], migration_weight: 0, skills_quality_rank: [] }
        : url.includes("circuit-breakers")
          ? { items: [] }
          : { items: [] };
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    });
    await page.goto(`/console/governance`);
    await expect(page.getByRole("main").getByRole("heading", { name: "Governance" })).toBeVisible();
    await expect(page.getByRole("button", { name: /day/i })).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole("heading", { name: "Budget Consumption Trend" })).toBeVisible({ timeout: 3000 });
  });

  test("Edge: malformed overview JSON does not white screen", async ({ page }) => {
    await page.route("**/api/v1/overview", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: "not valid json",
      })
    );
    await page.goto(`/console/`);
    await page.waitForTimeout(3000);
    await expect(page.getByRole("main")).toBeVisible();
    await expect(page.getByRole("main").getByRole("heading", { name: "Overview" })).toBeVisible();
    const failed = await page.getByText(/Failed to load overview metrics/i).isVisible();
    const loading = await page.getByText(/Loading overview/i).isVisible();
    expect(failed || loading).toBe(true);
  });
});


import { expect, Page, test } from "@playwright/test";

const appRoutes = [
  "/sources",
  "/governance",
  "/governance/review",
  "/ask",
  "/views/heatmap",
  "/admin/models",
  "/admin/access",
  "/quality/lint",
] as const;

const setupGovernanceWorkflowMocks = async (page: Page): Promise<void> => {
  await page.addInitScript(() => {
    globalThis.localStorage.setItem("margin_access_token", "header.payload.signature");
    globalThis.localStorage.setItem("margin_user", "governance-workflow-smoke");
    globalThis.localStorage.setItem("margin_groups", "architect");
  });
  page.on("dialog", (dialog) => {
    void dialog.accept();
  });
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();

    const reply = (payload: unknown): Promise<void> =>
      route.fulfill({
        body: JSON.stringify(payload),
        contentType: "application/json",
        status: 200,
      });

    if (path === "/api/dashboard/summary") {
      await reply({
        expired_waivers: 0,
        recent_adrs: [],
        review_tasks_assigned: 0,
        stale_claims: 0,
        top_debt: [],
        top_risks: [],
      });
      return;
    }
    if (path === "/api/dashboard/heatmap") {
      await reply({
        domains: [{ domain_id: "OSS", health: 92, risks: 1, debt: 1, stale_items: 0, waivers: 0 }],
      });
      return;
    }
    if (
      path === "/api/admin/models" ||
      path === "/api/admin/models/health" ||
      path === "/api/admin/models/configuration-checks"
    ) {
      await reply([]);
      return;
    }
    if (path === "/api/admin/access") {
      await reply([
        {
          user: "alice",
          groups: ["arch-admin"],
          domains: ["OSS", "Platform"],
          sensitivities: ["internal", "security-sensitive"],
        },
      ]);
      return;
    }
    if (path === "/api/admin/access/check" && method === "POST") {
      await reply({
        user: "bob",
        canSee: ["domain:OSS"],
        denied: ["domain:Security"],
        decisionTrace: "Evaluated scope tokens against stored grants.",
      });
      return;
    }
    if (path === "/api/audit-events") {
      await reply({ audit_events: [] });
      return;
    }
    if (path === "/api/admin/system-health") {
      await reply({
        status: "ready",
        database: "ready",
        converterDependencies: "ready",
        objectStorage: "configured",
        redis: "configured",
      });
      return;
    }
    if (path === "/api/governance/review/tasks") {
      await reply({ tasks: [] });
      return;
    }
    if (path === "/api/governance/records") {
      await reply({ records: [] });
      return;
    }
    if (path === "/api/jobs") {
      await reply({ jobs: [] });
      return;
    }
    if (path === "/api/sources") {
      await reply({ sources: [] });
      return;
    }
    if (path === "/api/governance/health/lint-report") {
      await reply({ findings: [], review_tasks_created: [] });
      return;
    }
    if (path === "/api/entities/catalog/ontology") {
      await reply({
        ontology_version: "margin-entity-ontology-v1",
        entity_types: ["domain", "system", "api", "component", "concept"],
        lifecycle_statuses: ["draft", "active", "superseded", "retired"],
      });
      return;
    }
    if (path === "/api/entities" || path === "/api/entities/") {
      await reply({
        items: [
          {
            id: "domain-oss",
            preferred_label: "OSS",
            ontology_type: "domain",
            ontology_version: "margin-entity-ontology-v1",
            catalog_version: 1,
            domain_id: "OSS",
            lifecycle_status: "active",
          },
          {
            id: "entity-1",
            preferred_label: "Order API",
            ontology_type: "api",
            ontology_version: "margin-entity-ontology-v1",
            catalog_version: 1,
            domain_id: "OSS",
            lifecycle_status: "active",
          },
        ],
        count: 2,
        unresolved_mentions: [],
        unresolved_count: 0,
      });
      return;
    }
    if (path === "/api/ask" && method === "POST") {
      await reply({
        answer: null,
        answer_unavailable_reason: "Some evidence was excluded due to permissions.",
        response_mode: "search_only",
        results: [],
        applied_filters: { statuses: ["verified"] },
        ambiguity: { entity_terms: [] },
      });
      return;
    }
    await reply({});
  });
};

test("governance workflow routes are reachable", async ({ page }) => {
  await setupGovernanceWorkflowMocks(page);

  await page.goto("/");
  await expect(page.getByText("Knowledge state summary")).toBeVisible();

  for (const route of appRoutes) {
    await page.goto(route);
    await expect(page.locator("h2").first()).toBeVisible();
  }
});

test("governance workflow: restricted evidence is denied without leaking quote", async ({
  page,
}) => {
  await setupGovernanceWorkflowMocks(page);

  await page.goto("/ask");
  await page.getByRole("main").getByRole("button", { name: "Ask Margin", exact: true }).click();

  await expect(
    page.getByText("Answer unavailable: Some evidence was excluded due to permissions.")
  ).toBeVisible();
  await expect(
    page.getByText("No authorized Idea Blocks matched this question and scope.")
  ).toBeVisible();
  await expect(page.getByText("TOP SECRET CUSTOMER TOKEN")).toHaveCount(0);
});

test("governance workflow: unified governance exposes focused records without parallel type pages", async ({
  page,
}) => {
  await page.addInitScript(() => {
    globalThis.localStorage.setItem("margin_access_token", "header.payload.signature");
    globalThis.localStorage.setItem("margin_user", "architect-smoke");
    globalThis.localStorage.setItem("margin_groups", "architect");
  });
  await setupGovernanceWorkflowMocks(page);

  await page.goto("/governance?focusedView=backlog");

  await expect(page.getByRole("heading", { name: "Governance records" })).toBeVisible();
  await expect(page.getByLabel("Focused view")).toHaveValue("backlog");
  await expect(page.getByText("No governance records match these filters.")).toBeVisible();
});

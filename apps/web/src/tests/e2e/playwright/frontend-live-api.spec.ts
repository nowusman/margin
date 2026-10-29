import { expect, Page, test } from "@playwright/test";

const json = (payload: unknown) => ({
  body: JSON.stringify(payload),
  contentType: "application/json",
  status: 200,
});

const setupMockApi = async (page: Page): Promise<void> => {
  await page.addInitScript(() => {
    globalThis.localStorage.setItem("margin_access_token", "header.payload.signature");
    globalThis.localStorage.setItem("margin_user", "live-api-smoke");
    globalThis.localStorage.setItem("margin_groups", "architect");
  });
  page.on("dialog", (dialog) => {
    void dialog.accept();
  });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();

    if (path === "/api/dashboard/summary") {
      await route.fulfill(
        json({
          review_tasks_assigned: 1,
          stale_claims: 1,
          expired_waivers: 0,
          recent_adrs: [],
          top_risks: [],
          top_debt: [],
        })
      );
      return;
    }
    if (path === "/api/dashboard/heatmap") {
      await route.fulfill(
        json({
          domains: [
            { domain_id: "OSS", health: 91, risks: 1, waivers: 0, debt: 1, stale_items: 1 },
          ],
        })
      );
      return;
    }
    if (path === "/api/governance/review/tasks/11111111-1111-1111-1111-111111111111/decision") {
      await route.fulfill(
        json({ task: { id: "11111111-1111-1111-1111-111111111111", status: "approved" } })
      );
      return;
    }
    if (path === "/api/governance/review/tasks/11111111-1111-1111-1111-111111111111") {
      await route.fulfill(
        json({
          task: {
            id: "11111111-1111-1111-1111-111111111111",
            task_type: "record_review",
            reason: "Verify owner",
            priority: "medium",
            status: "open",
            domain_id: "OSS",
            target_id: "gov-1",
            recommendation: "Verify the owner and decide.",
            comments: [],
            updated_at: "2026-05-19T00:00:00Z",
          },
          bundle: {
            task: {
              id: "11111111-1111-1111-1111-111111111111",
              status: "open",
              type: "record_review",
              reason: "Verify owner",
              updated_at: "2026-05-19T00:00:00Z",
              target_type: "governance_record",
              target_id: "gov-1",
            },
            claims: [],
            evidence: [],
            entity_resolution: null,
            semantic_resolution: null,
            source_version: null,
            context: { before: null, current: null, after: null },
          },
        })
      );
      return;
    }
    if (path === "/api/governance/review/tasks") {
      await route.fulfill(
        json({
          tasks: [
            { id: "11111111-1111-1111-1111-111111111111", status: "open", reason: "Verify owner" },
          ],
        })
      );
      return;
    }
    if (path.startsWith("/api/governance/review/tasks/") && method === "PATCH") {
      await route.fulfill(
        json({ task: { id: "11111111-1111-1111-1111-111111111111", status: "approved" } })
      );
      return;
    }
    if (path === "/api/governance/health/lint-report") {
      await route.fulfill(
        json({
          findings: [{ id: "lint-1", rule: "missing_evidence", summary: "Needs evidence" }],
          review_tasks_created: ["rv-2"],
        })
      );
      return;
    }
    if (path === "/api/jobs") {
      await route.fulfill(
        json({
          jobs: [
            {
              id: "job-1",
              source_document_id: "src-1",
              state: "failed",
              started_at: "2026-05-19T00:00:00Z",
            },
          ],
        })
      );
      return;
    }
    if (path === "/api/sources") {
      await route.fulfill(json({ sources: [{ id: "src-1", title: "Customer platform HLD" }] }));
      return;
    }
    if (path === "/api/sources/src-1") {
      await route.fulfill(
        json({
          source_document: {
            id: "src-1",
            title: "Customer platform HLD",
            domain_id: "OSS",
            category: "Design Document",
            sensitivity: "internal",
            status: "knowledge_ready",
          },
        })
      );
      return;
    }
    if (path === "/api/sources/src-1/pages") {
      await route.fulfill(json({ pages: [{ id: "page-1", page_number: 1 }] }));
      return;
    }
    if (path === "/api/sources/src-1/elements") {
      await route.fulfill(json({ elements: [{ id: "elem-1", source_page_id: "page-1" }] }));
      return;
    }
    if (path === "/api/ask") {
      await route.fulfill(
        json({
          answer: "Use PostgreSQL, cited from ADR evidence.",
          citations: [
            {
              citation_id: "elem-1",
              source_document_title: "ADR-001",
              source_page_id: "page-1",
              source_element_id: "elem-1",
            },
          ],
        })
      );
      return;
    }
    if (path === "/api/entities/catalog/ontology") {
      await route.fulfill(
        json({
          ontology_version: "margin-entity-ontology-v1",
          entity_types: ["domain", "system", "api", "component", "concept"],
          lifecycle_statuses: ["draft", "active", "superseded", "retired"],
        })
      );
      return;
    }
    if (path === "/api/entities") {
      await route.fulfill(
        json({
          items: [
            {
              id: "entity-1",
              preferred_label: "Order API",
              ontology_type: "api",
              ontology_version: "margin-entity-ontology-v1",
              catalog_version: 1,
              lifecycle_status: "active",
            },
          ],
          count: 1,
          unresolved_mentions: [],
          unresolved_count: 0,
        })
      );
      return;
    }
    if (path === "/api/admin/models") {
      await route.fulfill(
        json([
          {
            id: "model-1",
            displayName: "Primary Inference Provider",
            purpose: "extraction",
            runtime: "openai-compatible",
            modelName: "configured-model",
            endpointUrl: "https://provider.example/v1",
            secretConfigured: false,
            capabilities: {},
            contextLength: 4096,
            maxTokens: 1024,
            temperatureDefault: 0.1,
            license: "model-specific",
            hardwareProfile: "configured-runtime",
            status: "active",
            defaultScopes: ["internal"],
          },
        ])
      );
      return;
    }
    if (path === "/api/admin/models/model-1" || path === "/api/admin/models/inference-narrator") {
      await route.fulfill(json({ status: "updated" }));
      return;
    }
    if (path === "/api/admin/models/health") {
      await route.fulfill(
        json([{ provider: "openai-compatible", status: "healthy", detail: "provider" }])
      );
      return;
    }
    if (path === "/api/admin/models/configuration-checks" || path === "/api/admin/access") {
      await route.fulfill(json([]));
      return;
    }
    if (path === "/api/audit-events") {
      await route.fulfill(json({ audit_events: [] }));
      return;
    }
    if (path === "/api/admin/system-health") {
      await route.fulfill(
        json({
          status: "ready",
          database: "ready",
          converterDependencies: "ready",
          objectStorage: "configured",
          redis: "configured",
        })
      );
      return;
    }
    await route.fulfill(json({}));
  });
};

test("core frontend journeys use live client calls against mocked backend responses", async ({
  page,
}) => {
  await setupMockApi(page);

  await page.goto("/ask");
  await page.getByRole("main").getByRole("button", { name: "Ask Margin", exact: true }).click();
  await expect(page.getByText("Use PostgreSQL, cited from ADR evidence.")).toBeVisible();

  await page.goto("/sources");
  await expect(page.getByText("Customer platform HLD").first()).toBeVisible();
  await page
    .getByRole("row")
    .filter({ hasText: "Customer platform HLD" })
    .getByRole("button", { name: "Open", exact: true })
    .click();
  await page.getByRole("button", { name: "Retry latest failed job" }).click();
  await expect(page.getByRole("status")).toContainText("Retry latest failed job succeeded.");

  await page.goto("/governance/review");
  await expect(page.getByText("Verify owner")).toBeVisible();
  await page
    .getByRole("row")
    .filter({ hasText: "Verify owner" })
    .getByRole("button", { name: "Open", exact: true })
    .click();
  await page.getByLabel("Review note / resolution").fill("Owner confirmed by the domain steward.");
  await page.getByRole("button", { name: "Approve bundle", exact: true }).click();
  await expect(page.getByText("approved review bundle succeeded.")).toBeVisible();
});

import { expect, Page, test } from "@playwright/test";
import { Buffer } from "node:buffer";

const json = (payload: unknown) => ({
  body: JSON.stringify(payload),
  contentType: "application/json",
  status: 200,
});

type ObservedCalls = {
  uploads: number;
  askBodies: Array<Record<string, unknown>>;
  reviewPatches: Array<Record<string, unknown>>;
  reviewDecisions: Array<Record<string, unknown>>;
  ideaBlockStatuses: string[];
};

const setupKnowledgeWorkflowMockApi = async (page: Page): Promise<ObservedCalls> => {
  await page.addInitScript(() => {
    globalThis.localStorage.setItem("margin_access_token", "header.payload.signature");
    globalThis.localStorage.setItem("margin_user", "knowledge-workflow-smoke");
    globalThis.localStorage.setItem("margin_groups", "architect");
  });
  page.on("dialog", (dialog) => {
    void dialog.accept();
  });
  const observed: ObservedCalls = {
    uploads: 0,
    askBodies: [],
    reviewPatches: [],
    reviewDecisions: [],
    ideaBlockStatuses: [],
  };

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();

    if (path === "/api/sources" && method === "POST") {
      observed.uploads += 1;
      await route.fulfill(json({ source_document: { id: "src-1" }, status: "accepted" }));
      return;
    }
    if (path === "/api/sources") {
      await route.fulfill(
        json({
          sources: [
            {
              id: "src-1",
              title: "Customer platform HLD",
              domain_id: "OSS",
              category: "Design Document",
              sensitivity: "internal",
              status: "ready",
              owner: "team-oss",
            },
          ],
        })
      );
      return;
    }
    if (path === "/api/sources/src-1") {
      await route.fulfill(
        json({
          id: "src-1",
          title: "Customer platform HLD",
          domain_id: "OSS",
          category: "Design Document",
          sensitivity: "internal",
          status: "ready",
          owner: "team-oss",
        })
      );
      return;
    }
    if (path === "/api/sources/src-1/pages") {
      await route.fulfill(
        json({
          pages: [
            {
              id: "page-1",
              page_number: 4,
              content_checksum: "page-checksum",
              classification: "text",
              summary: "Primary architecture",
            },
          ],
        })
      );
      return;
    }
    if (path === "/api/sources/src-1/elements") {
      await route.fulfill(
        json({
          elements: [
            {
              id: "elem-1",
              source_page_id: "page-1",
              kind: "quote",
              text: "Element quote for architecture overview.",
              page_number: 4,
              char_start: 10,
              char_end: 42,
              bounding_box: { x: 10, y: 10, width: 100, height: 20 },
              checksum: "abc",
            },
          ],
        })
      );
      return;
    }
    if (path === "/api/sources/src-1/extraction-report") {
      await route.fulfill(json({ report: { candidate_count: 1 } }));
      return;
    }
    if (path === "/api/sources/src-1/versions") {
      await route.fulfill(json({ versions: [{ id: "v1", versionHash: "hash-1" }] }));
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
              attempts: 1,
              quality_report: {
                warnings: ["failed on extraction"],
                logs: ["retry supported"],
                output_counts: { elements: 1 },
              },
            },
          ],
        })
      );
      return;
    }
    if (path === "/api/jobs/job-1") {
      await route.fulfill(
        json({
          ingestion_job: {
            id: "job-1",
            source_document_id: "src-1",
            state: "failed",
            attempts: 1,
            quality_report: {
              warnings: ["failed on extraction"],
              logs: ["retry supported"],
              output_counts: { elements: 1 },
            },
          },
        })
      );
      return;
    }
    if (path === "/api/jobs/job-1/retry") {
      await route.fulfill(json({ status: "retried" }));
      return;
    }
    if (path === "/api/jobs/job-1/cancel") {
      await route.fulfill(json({ status: "cancelled" }));
      return;
    }
    if (path === "/api/sources/src-1/reprocess") {
      await route.fulfill(json({ status: "reprocessed" }));
      return;
    }
    if (path === "/api/ideablocks") {
      await route.fulfill(
        json({
          idea_blocks: [
            {
              id: "ib-1",
              statement: "Entity evidence statement",
              category: "claim",
              type: "fact",
              confidence: "0.93",
              status: "candidate",
              source_page_id: "page-1",
              source_element_id: "elem-1",
              source_quote: "Element quote for architecture overview.",
              metadata: { extraction_method: "deterministic" },
            },
          ],
        })
      );
      return;
    }
    if (path === "/api/ideablocks/ib-1") {
      await route.fulfill(
        json({
          idea_block: {
            id: "ib-1",
            statement: "Entity evidence statement",
            category: "claim",
            type: "fact",
            confidence: "0.93",
            status: "candidate",
            source_page_id: "page-1",
            source_element_id: "elem-1",
            source_quote: "Element quote for architecture overview.",
            metadata: { extraction_method: "deterministic" },
          },
        })
      );
      return;
    }
    if (path === "/api/ideablocks/ib-1/status" && method === "PATCH") {
      const body = request.postDataJSON() as { status?: string };
      observed.ideaBlockStatuses.push(String(body.status ?? ""));
      await route.fulfill(
        json({
          idea_block: {
            id: "ib-1",
            statement: "Entity evidence statement",
            category: "claim",
            type: "fact",
            confidence: "0.93",
            status: body.status ?? "candidate",
            source_page_id: "page-1",
            source_element_id: "elem-1",
            source_quote: "Element quote for architecture overview.",
            metadata: { extraction_method: "deterministic" },
          },
        })
      );
      return;
    }
    if (path === "/api/governance/review/tasks") {
      await route.fulfill(
        json({
          tasks: [
            {
              id: "review-1",
              task_type: "candidate",
              reason: "Review candidate IdeaBlock",
              priority: "high",
              status: "open",
              target_id: "ib-1",
              evidence: { idea_block_id: "ib-1" },
              recommendation: "Approve or reject",
              comments: [],
            },
          ],
        })
      );
      return;
    }
    if (path === "/api/governance/review/tasks/review-1/decision") {
      const body = request.postDataJSON() as Record<string, unknown>;
      observed.reviewDecisions.push(body);
      await route.fulfill(json({ task: { id: "review-1", status: body.decision ?? "approved" } }));
      return;
    }
    if (path === "/api/governance/review/tasks/review-1") {
      if (method === "PATCH") {
        const body = request.postDataJSON() as Record<string, unknown>;
        observed.reviewPatches.push(body);
        await route.fulfill(json({ task: { id: "review-1", status: body.status ?? "updated" } }));
        return;
      }
      await route.fulfill(
        json({
          task: {
            id: "review-1",
            task_type: "candidate",
            reason: "Review candidate IdeaBlock",
            priority: "high",
            status: "open",
            target_id: "ib-1",
            evidence: { idea_block_id: "ib-1" },
            recommendation: "Approve or reject",
            comments: [],
          },
        })
      );
      return;
    }
    if (path === "/api/governance/contradictions/detect") {
      await route.fulfill(json({ review_tasks: [] }));
      return;
    }
    if (path === "/api/governance/blind-spots/detect") {
      await route.fulfill(json({ review_tasks: [] }));
      return;
    }
    if (path === "/api/governance/health/lint-report") {
      await route.fulfill(json({ findings: [], review_tasks_created: [] }));
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
    if (path === "/api/entities" || path === "/api/entities/") {
      await route.fulfill(
        json({
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
            {
              id: "container-1",
              preferred_label: "Order Service",
              ontology_type: "system",
              ontology_version: "margin-entity-ontology-v1",
              catalog_version: 1,
              domain_id: "OSS",
              lifecycle_status: "active",
            },
            {
              id: "component-1",
              preferred_label: "Order Repository",
              ontology_type: "component",
              ontology_version: "margin-entity-ontology-v1",
              catalog_version: 1,
              domain_id: "OSS",
              lifecycle_status: "active",
            },
          ],
          count: 4,
          unresolved_mentions: [],
          unresolved_count: 0,
        })
      );
      return;
    }
    if (path === "/api/entities/entity-1") {
      await route.fulfill(
        json({
          id: "entity-1",
          preferred_label: "Order API",
          normalized_label: "order api",
          ontology_type: "api",
          ontology_version: "margin-entity-ontology-v1",
          catalog_version: 1,
          domain_id: "OSS",
          locale: "en",
          lifecycle_status: "active",
          aliases: [],
          idea_blocks: [{ idea_block_id: "ib-1", role: "subject", current_revision_id: "ibr-1" }],
          relationships: [],
          governance_records: [],
          source_mentions: [],
          resolution_history: [],
        })
      );
      return;
    }
    if (path === "/api/dashboard/heatmap") {
      await route.fulfill(
        json({
          domains: [
            { domain_id: "OSS", health: 91, risks: 0, waivers: 0, debt: 1, stale_items: 0 },
          ],
        })
      );
      return;
    }
    if (path === "/api/domains") {
      await route.fulfill(
        json({
          domains: [
            {
              id: "OSS",
              name: "OSS",
              owner: "team-oss",
              steward: "architecture",
              status: "active",
            },
          ],
        })
      );
      return;
    }
    if (path === "/api/diagrams") {
      await route.fulfill(
        json({
          diagrams: [
            {
              id: "diagram-structurizr-customer-platform",
              type: "structurizr",
              title: "Customer Platform Context",
              evidence: [
                { source_path: "tests/fixtures/sample-data/diagrams/customer-platform.dsl" },
              ],
            },
          ],
        })
      );
      return;
    }
    if (path === "/api/diagrams/diagram-structurizr-customer-platform") {
      await route.fulfill(
        json({
          id: "diagram-structurizr-customer-platform",
          type: "structurizr",
          title: "Customer Platform Context",
          markup:
            'workspace "Customer Platform" { model { system = softwareSystem "Customer Platform" } }',
          evidence: [{ source_path: "tests/fixtures/sample-data/diagrams/customer-platform.dsl" }],
        })
      );
      return;
    }
    if (path === "/api/ask" && method === "POST") {
      observed.askBodies.push((request.postDataJSON() as Record<string, unknown>) ?? {});
      await route.fulfill(
        json({
          answer: "Use PostgreSQL, cited from ADR evidence.",
          response_mode: "hybrid",
          results: [
            {
              idea_block_id: "ib-1",
              idea_block_revision_id: "ibr-1",
              statement: "Use PostgreSQL for transactional consistency.",
              type: "decision",
              status: "verified",
              polarity: "supporting",
              score: 0.93,
              entities: [],
              citations: [
                {
                  citation_id: "cite-1",
                  idea_block_id: "ib-1",
                  idea_block_revision_id: "ibr-1",
                  source_document_id: "src-1",
                  source_document_title: "ADR-001",
                  source_element_id: "elem-1",
                  page_number: 4,
                  quote: "Use PostgreSQL for transactional consistency.",
                  confidence: "high",
                },
              ],
              contradictions: [],
              supersession: [],
              governance_records: [],
            },
          ],
          applied_filters: { statuses: ["verified"] },
          ambiguity: { entity_terms: [] },
        })
      );
      return;
    }
    if (path === "/api/evidence/ib-1") {
      await route.fulfill(
        json({
          idea_block: {
            id: "ib-1",
            statement: "Entity evidence statement",
            category: "claim",
            type: "fact",
            confidence: "0.93",
            status: "verified",
            source_page_id: "page-1",
            source_element_id: "elem-1",
            source_quote: "Use PostgreSQL for transactional consistency.",
            metadata: { extraction_method: "retrieval", source_document_id: "src-1" },
          },
        })
      );
      return;
    }
    if (path === "/api/audit-events") {
      await route.fulfill(json({ audit_events: [] }));
      return;
    }

    await route.fulfill(json({}));
  });

  return observed;
};

test("knowledge workflow: upload source, inspect source detail, ask cited evidence", async ({
  page,
}) => {
  const observed = await setupKnowledgeWorkflowMockApi(page);

  await page.goto("/sources");
  await page.locator("input[type='file']").setInputFiles({
    name: "architecture.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# Architecture\n"),
  });
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByRole("button", { name: "Start upload" }).click();
  await expect.poll(() => observed.uploads).toBeGreaterThanOrEqual(1);

  await page.goto("/ask");
  await page.getByRole("main").getByRole("button", { name: "Ask Margin", exact: true }).click();
  await expect(page.getByText("Use PostgreSQL, cited from ADR evidence.")).toBeVisible();
  await page.getByRole("button", { name: "ADR-001 - p.4" }).click();
  await expect(
    page
      .getByRole("blockquote")
      .filter({ hasText: "Use PostgreSQL for transactional consistency." })
  ).toBeVisible();

  expect(observed.uploads).toBeGreaterThanOrEqual(1);
  expect(observed.askBodies[0]).toMatchObject({
    query: expect.any(String),
    top_k: expect.any(Number),
  });
});

test("knowledge workflow: review actions and candidate IdeaBlock lifecycle hit expected endpoints", async ({
  page,
}) => {
  const observed = await setupKnowledgeWorkflowMockApi(page);

  await page.goto("/governance/review");
  await page.getByRole("button", { name: "Open", exact: true }).click();
  await expect(page.getByRole("button", { name: "Approve bundle", exact: true })).toBeVisible();
  await page.getByLabel("Review note / resolution").fill("Need stronger evidence for this claim.");
  await page.getByRole("button", { name: "Approve bundle", exact: true }).click();
  await expect
    .poll(() => observed.reviewDecisions.map((item) => item.decision))
    .toContain("approved");

  await page.goto("/knowledge/ideablocks");
  await page.getByRole("button", { name: "Open IdeaBlock ib-1", exact: true }).click();
  await expect(page.getByRole("button", { name: "Approve", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await page.getByRole("button", { name: "Reject", exact: true }).click();

  expect(observed.reviewDecisions.map((item) => item.decision)).toContain("approved");
});

test("knowledge workflow: heatmap to entity evidence", async ({ page }) => {
  await setupKnowledgeWorkflowMockApi(page);

  await page.goto("/views/heatmap");
  await page.getByRole("button", { name: "OSS", exact: true }).click();
  await expect(page.getByText("Selected OSS")).toBeVisible();
  await page.getByRole("button", { name: "Open selected domain detail" }).click();
  await expect(page).toHaveURL(/\/knowledge\/domains\/OSS$/);
  await expect(page.getByRole("dialog", { name: "Domain record" })).toBeVisible();

  await page.goto("/views/c4/entity-1");
  await expect(page.getByText("Evidence: Entity evidence statement (candidate)")).toBeVisible();
  await page.getByRole("button", { name: "structurizr: Customer Platform Context" }).click();
  await expect(
    page.getByText("Evidence: tests/fixtures/sample-data/diagrams/customer-platform.dsl")
  ).toBeVisible();
  await expect(page.getByText("Type: structurizr")).toBeVisible();
});

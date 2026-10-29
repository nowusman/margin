import { expect, Page, test } from "@playwright/test";

const setupHardeningMocks = async (page: Page): Promise<void> => {
  await page.addInitScript(() => {
    globalThis.localStorage.setItem("margin_access_token", "header.payload.signature");
    globalThis.localStorage.setItem("margin_user", "hardening-smoke");
    globalThis.localStorage.setItem("margin_groups", "architect");
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;

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
        domains: [{ domain_id: "OSS", health: 90, risks: 0, debt: 0, stale_items: 0, waivers: 0 }],
      });
      return;
    }
    if (path === "/api/ask") {
      await reply({
        answer: "No evidence found.",
        answerType: "explanation",
        state: "insufficient_evidence",
        citations: [],
        excluded_by_permissions: 0,
        related_governance: [],
      });
      return;
    }
    if (path === "/api/governance/review/tasks") {
      await reply({ tasks: [] });
      return;
    }
    if (path === "/api/jobs") {
      await reply({ jobs: [] });
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
    if (path === "/api/entities") {
      await reply({ items: [], count: 0, unresolved_mentions: [], unresolved_count: 0 });
      return;
    }
    if (
      path === "/api/admin/models" ||
      path === "/api/admin/models/health" ||
      path === "/api/admin/models/configuration-checks" ||
      path === "/api/admin/access"
    ) {
      await reply([]);
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
    if (path === "/api/audit-events") {
      await reply({ audit_events: [] });
      return;
    }
    if (path === "/api/graph/subgraph") {
      await reply({
        nodes: [
          {
            id: "ent-payment-api",
            label: "Payment API",
            kind: "system",
            domain_id: "OSS",
            definition: "Accepts payment requests.",
            degree: 1,
            community_id: "community-aaa",
            ontology_version: "margin-ontology-test",
          },
          {
            id: "ent-ledger",
            label: "Ledger",
            kind: "data_store",
            domain_id: "OSS",
            definition: "Stores settled payments.",
            degree: 1,
            community_id: "community-aaa",
            ontology_version: "margin-ontology-test",
          },
        ],
        edges: [
          {
            id: "rel-payment-ledger",
            source_id: "ent-payment-api",
            target_id: "ent-ledger",
            relation_type: "uses",
            confidence: "verified",
            status: "verified",
            valid_from: "",
            valid_to: "",
            quotes: ["The payment API writes settled payments to the ledger."],
            idea_block_ids: ["ib-payment-ledger"],
          },
        ],
        communities: [
          {
            community_id: "community-aaa",
            member_ids: ["ent-payment-api", "ent-ledger"],
            internal_edge_count: 1,
            boundary_edge_count: 0,
            cohesion: 1,
            mean_intra_degree: 1,
          },
        ],
        capped: false,
        depth_reached: 1,
        ontology_version: "margin-ontology-test",
        scope: { entity_count: 2, root_entity_id: null, depth: 1 },
      });
      return;
    }
    if (path === "/api/graph/insights") {
      await reply({
        surprising_connections: [],
        knowledge_gaps: [],
        hidden_count: 0,
        ontology_version: "margin-ontology-test",
      });
      return;
    }
    if (path === "/api/personal/feed") {
      await reply({ feed: [], queue: [], permitted_domains: ["OSS"] });
      return;
    }
    if (path === "/api/personal/notes") {
      await reply({ notes: [] });
      return;
    }
    if (path === "/api/personal/saved") {
      await reply({ saved_items: [] });
      return;
    }
    if (path === "/api/personal/threads") {
      await reply({ threads: [] });
      return;
    }
    if (path === "/api/domains") {
      await reply({ domains: [{ id: "OSS", name: "Operating Systems", status: "active" }] });
      return;
    }
    await reply({});
  });
};

const checkAccessibilityBasics = async (
  page: Page
): Promise<{ unlabeledControls: string[]; unlabeledButtons: number }> =>
  page.evaluate(() => {
    const controls = Array.from(document.querySelectorAll("input, select, textarea"));
    const unlabeledControls = controls
      .filter((control) => {
        const type = control.getAttribute("type");
        if (type === "hidden") return false;
        if (control.getAttribute("aria-label") || control.getAttribute("aria-labelledby"))
          return false;
        if (control.closest("label")) return false;
        const id = control.getAttribute("id");
        if (!id) return true;
        return !document.querySelector(`label[for="${id}"]`);
      })
      .map((control) => {
        const tag = control.tagName.toLowerCase();
        const id = control.getAttribute("id") || "no-id";
        const name = control.getAttribute("name") || "no-name";
        return `${tag}:${id}:${name}`;
      });

    const unlabeledButtons = Array.from(document.querySelectorAll("button")).filter((button) => {
      const hasText = Boolean(button.textContent && button.textContent.trim());
      const hasAria = Boolean(
        button.getAttribute("aria-label") || button.getAttribute("aria-labelledby")
      );
      return !hasText && !hasAria;
    }).length;

    return { unlabeledControls, unlabeledButtons };
  });

test("@a11y frontend hardening: critical admin, ask, graph and personal controls are labeled", async ({
  page,
}) => {
  await setupHardeningMocks(page);

  for (const route of [
    "/ask",
    "/admin/models",
    "/admin/access",
    "/knowledge/entities",
    "/admin/system",
    "/views/graph",
    "/me",
  ]) {
    await page.goto(route);
    const report = await checkAccessibilityBasics(page);
    expect(
      report.unlabeledControls,
      `Unlabeled controls on ${route}: ${report.unlabeledControls.join(", ")}`
    ).toEqual([]);
    expect(report.unlabeledButtons, `Unlabeled buttons on ${route}`).toBe(0);
  }
});

test("@perf frontend hardening: home dashboard load stays within local budget", async ({
  page,
}) => {
  await setupHardeningMocks(page);

  await page.goto("/");

  const metrics = await page.evaluate(() => {
    const nav = window.performance.getEntriesByType("navigation")[0];
    return {
      domContentLoadedMs: nav ? nav.domContentLoadedEventEnd : 0,
      loadMs: nav ? nav.loadEventEnd : 0,
      resourceCount: window.performance.getEntriesByType("resource").length,
    };
  });

  expect(metrics.domContentLoadedMs).toBeLessThan(6000);
  expect(metrics.loadMs).toBeLessThan(9000);
  expect(metrics.resourceCount).toBeLessThan(250);
});

test("@perf frontend hardening: the graph map renders within a local budget", async ({ page }) => {
  await setupHardeningMocks(page);

  await page.goto("/views/graph");
  await expect(page.getByTestId("graph-map-canvas")).toBeVisible();

  const metrics = await page.evaluate(() => {
    const nav = window.performance.getEntriesByType("navigation")[0];
    return {
      domContentLoadedMs: nav ? nav.domContentLoadedEventEnd : 0,
      loadMs: nav ? nav.loadEventEnd : 0,
      resourceCount: window.performance.getEntriesByType("resource").length,
    };
  });

  expect(metrics.domContentLoadedMs).toBeLessThan(6000);
  expect(metrics.loadMs).toBeLessThan(9000);
  expect(metrics.resourceCount).toBeLessThan(250);
});

import { expect, Page, test } from "@playwright/test";

const json = (payload: unknown) => ({
  body: JSON.stringify(payload),
  contentType: "application/json",
  status: 200,
});

const SUBGRAPH = {
  nodes: [
    {
      id: "ent-payment-api",
      label: "Payment API",
      kind: "system",
      domain_id: "OSS",
      definition: "Accepts payment requests.",
      degree: 2,
      community_id: "community-aaa",
    },
    {
      id: "ent-ledger",
      label: "Ledger",
      kind: "data_store",
      domain_id: "OSS",
      definition: "Stores settled payments.",
      degree: 2,
      community_id: "community-aaa",
    },
    {
      id: "ent-orphaned-report",
      label: "Quarterly Report",
      kind: "concept",
      domain_id: "OSS",
      definition: "Nothing points at this yet.",
      degree: 0,
      community_id: "community-bbb",
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
      valid_from: null,
      valid_to: null,
      quotes: ["The payment API writes settled payments to the ledger."],
      idea_block_ids: ["ib-payment-ledger"],
    },
    {
      id: "rel-ledger-report",
      source_id: "ent-ledger",
      target_id: "ent-orphaned-report",
      relation_type: "references",
      confidence: "medium",
      status: "candidate",
      valid_from: null,
      valid_to: null,
      quotes: ["The ledger is referenced by the quarterly report."],
      idea_block_ids: ["ib-ledger-report"],
    },
  ],
  communities: [
    {
      community_id: "community-aaa",
      member_ids: ["ent-payment-api", "ent-ledger"],
      internal_edge_count: 1,
      boundary_edge_count: 0,
      cohesion: 1.0,
      mean_intra_degree: 1.0,
    },
    {
      community_id: "community-bbb",
      member_ids: ["ent-orphaned-report"],
      internal_edge_count: 0,
      boundary_edge_count: 0,
      cohesion: 0.0,
      mean_intra_degree: 0.0,
    },
  ],
  capped: false,
  depth_reached: 1,
  ontology_version: "margin-ontology-test",
  scope: { entity_count: 3, root_entity_id: null, depth: 1 },
};

const EXPANDED_SUBGRAPH = {
  ...SUBGRAPH,
  nodes: [
    ...SUBGRAPH.nodes,
    {
      id: "ent-settlement",
      label: "Settlement Service",
      kind: "service",
      domain_id: "OSS",
      definition: "Settles the ledger overnight.",
      degree: 1,
      community_id: "community-ccc",
    },
  ],
  edges: [
    ...SUBGRAPH.edges,
    {
      id: "rel-ledger-settlement",
      source_id: "ent-ledger",
      target_id: "ent-settlement",
      relation_type: "depends_on",
      confidence: "verified",
      status: "verified",
      valid_from: null,
      valid_to: null,
      quotes: ["The ledger depends on the settlement service."],
      idea_block_ids: ["ib-ledger-settlement"],
    },
  ],
};

const INSIGHTS = {
  surprising_connections: [
    {
      edge_id: "rel-payment-ledger",
      source_id: "ent-payment-api",
      target_id: "ent-ledger",
      score: 3,
      reasons: ["crosses community boundary", "connects system to data_store"],
    },
  ],
  knowledge_gaps: [
    {
      gap_key: "isolated-record:block-3",
      gap_type: "isolated-record",
      title: "1 isolated record",
      description: "Quarterly Report",
      node_ids: ["ent-orphaned-report"],
      suggestion: "Connect these records to related records.",
      research_term: "Quarterly Report",
      research_context: "",
      affected_records: [{ id: "ent-orphaned-report", label: "Quarterly Report", kind: "concept" }],
    },
  ],
  hidden_count: 0,
  ontology_version: "margin-ontology-test",
};

const setupGraphMockApi = async (
  page: Page
): Promise<{
  decisions: Array<Record<string, unknown>>;
  subgraphRequests: string[];
  reviewTasks: Array<Record<string, unknown>>;
}> => {
  await page.addInitScript(() => {
    globalThis.localStorage.setItem("margin_access_token", "header.payload.signature");
    globalThis.localStorage.setItem("margin_user", "graph-smoke");
    globalThis.localStorage.setItem("margin_groups", "architect");
  });
  const observed = {
    decisions: [] as Array<Record<string, unknown>>,
    subgraphRequests: [] as string[],
    reviewTasks: [] as Array<Record<string, unknown>>,
  };

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();

    if (path === "/api/graph/subgraph") {
      const url = request.url();
      observed.subgraphRequests.push(url);
      await route.fulfill(
        json(url.includes("root_entity_id=ent-ledger") ? EXPANDED_SUBGRAPH : SUBGRAPH)
      );
      return;
    }
    if (path === "/api/graph/insights") {
      await route.fulfill(json(INSIGHTS));
      return;
    }
    if (path === "/api/graph/insights/decisions" && method === "POST") {
      const body = JSON.parse(request.postData() ?? "{}") as Record<string, unknown>;
      observed.decisions.push(body);
      await route.fulfill(
        json({
          status: body.decision,
          target_id: body.target_id ?? "",
          target_type: body.target_type ?? "connection",
          deferred_until: null,
        })
      );
      return;
    }
    if (path === "/api/governance/review/tasks" && method === "POST") {
      const body = JSON.parse(request.postData() ?? "{}") as Record<string, unknown>;
      observed.reviewTasks.push(body);
      await route.fulfill(json({ task: { id: "task-1", status: "open" } }));
      return;
    }
    await route.fulfill(json({}));
  });

  return observed;
};

test("graph workflow: the readable graph renders records, communities and gaps", async ({
  page,
}) => {
  await setupGraphMockApi(page);

  await page.goto("/views/graph");

  await expect(page.getByTestId("graph-map-canvas")).toBeVisible();
  await expect(page.getByTestId("graph-map-node")).toHaveCount(3);
  const records = page.getByTestId("graph-map-records");
  await expect(records.getByText("Payment API", { exact: true })).toBeVisible();
  await expect(records.getByText("Quarterly Report", { exact: true })).toBeVisible();

  await expect(page.getByTestId("graph-community")).toHaveCount(2);
  await expect(page.getByText("2 records", { exact: true })).toBeVisible();

  await expect(page.getByTestId("graph-gap")).toHaveCount(1);
  await expect(page.getByText("1 isolated record", { exact: true })).toBeVisible();
  await expect(page.getByTestId("graph-gap-research")).toBeVisible();
  await expect(page.getByTestId("graph-gap-records")).toContainText("Quarterly Report");

  await expect(page.getByTestId("graph-surprise")).toHaveCount(1);
  await expect(page.getByText("score 3", { exact: true })).toBeVisible();
});

test("graph workflow: the view filters records by kind and relationship", async ({ page }) => {
  await setupGraphMockApi(page);

  await page.goto("/views/graph");

  await page.getByLabel("Graph kind filter").selectOption("system");
  await expect(page.getByTestId("graph-record")).toHaveCount(1);
  await expect(
    page.getByTestId("graph-record-list").getByText("Payment API", { exact: true })
  ).toBeVisible();

  await page.getByLabel("Graph kind filter").selectOption([]);
  await page.getByLabel("Graph relationship filter").selectOption("uses");
  await expect(page.getByTestId("graph-record")).toHaveCount(2);

  await page.getByLabel("Graph relationship filter").selectOption([]);
  await page.getByLabel("Graph knowledge state filter").selectOption("candidate");
  await expect(page.getByTestId("graph-record")).toHaveCount(2);
  await expect(
    page.getByTestId("graph-record-list").getByText("Quarterly Report", { exact: true })
  ).toBeVisible();
  await expect(page.getByTestId("graph-record-list")).not.toContainText("Payment API");

  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(page.getByTestId("graph-record")).toHaveCount(3);
});

test("graph workflow: a record expands to reveal its neighbourhood", async ({ page }) => {
  const observed = await setupGraphMockApi(page);

  await page.goto("/views/graph");
  await page
    .getByTestId("graph-record")
    .filter({ hasText: "Ledger" })
    .getByTestId("graph-record-expand")
    .click();

  await expect(
    page.getByTestId("graph-record-list").getByText("Settlement Service", { exact: true })
  ).toBeVisible();
  expect(observed.subgraphRequests.some((url) => url.includes("root_entity_id=ent-ledger"))).toBe(
    true
  );
});

test("graph workflow: a dismissed connection is recorded for the reader", async ({ page }) => {
  const observed = await setupGraphMockApi(page);

  await page.goto("/views/graph");
  await page.getByRole("button", { name: "Dismiss", exact: true }).click();

  await expect
    .poll(() => observed.decisions.map((decision) => decision.target_id))
    .toContain("rel-payment-ledger");
  expect(observed.decisions[0]?.decision).toBe("dismiss");
  expect(observed.decisions[0]?.target_type).toBe("connection");
});

test("graph workflow: a focused graph asks for the record neighbourhood", async ({ page }) => {
  const observed = await setupGraphMockApi(page);

  await page.goto("/views/graph/ent-payment-api");

  await expect(page.getByTestId("graph-map-canvas")).toBeVisible();
  await expect.poll(() => observed.subgraphRequests.length).toBeGreaterThan(0);
  expect(
    observed.subgraphRequests.some((url) => url.includes("root_entity_id=ent-payment-api"))
  ).toBe(true);
});

test("graph workflow: a knowledge gap can be dismissed from the review surface", async ({
  page,
}) => {
  const observed = await setupGraphMockApi(page);

  await page.goto("/views/graph");
  await page.getByTestId("graph-gap-dismiss").click();

  await expect.poll(() => observed.decisions.length).toBe(1);
  expect(observed.decisions[0]?.target_id).toBe("isolated-record:block-3");
  expect(observed.decisions[0]?.target_type).toBe("gap");
  expect(observed.decisions[0]?.decision).toBe("dismiss");
});

test("graph workflow: a knowledge gap can be deferred for later", async ({ page }) => {
  const observed = await setupGraphMockApi(page);

  await page.goto("/views/graph");
  await page.getByTestId("graph-gap-defer").click();

  await expect.poll(() => observed.decisions.length).toBe(1);
  expect(observed.decisions[0]?.target_id).toBe("isolated-record:block-3");
  expect(observed.decisions[0]?.decision).toBe("defer");
});

test("graph workflow: a knowledge gap can become a research task", async ({ page }) => {
  const observed = await setupGraphMockApi(page);

  await page.goto("/views/graph");
  await page.getByTestId("graph-gap-create").click();

  await expect.poll(() => observed.reviewTasks.length).toBe(1);
  expect(observed.reviewTasks[0]?.target_type).toBe("knowledge_gap");
  expect(observed.reviewTasks[0]?.target_id).toBe("isolated-record:block-3");
});

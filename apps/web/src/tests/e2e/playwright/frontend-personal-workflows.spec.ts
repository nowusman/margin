import { expect, Page, test } from "@playwright/test";

const json = (payload: unknown) => ({
  body: JSON.stringify(payload),
  contentType: "application/json",
  status: 200,
});

const FEED = {
  feed: [
    {
      target_type: "idea_block",
      target_id: "block-1",
      target_label: "The payment API writes settled payments to the ledger.",
      target_kind: "Idea Block",
      provenance: ["Payment architecture.md"],
      reason: "you read this",
      viewed_at: "2026-10-27T09:00:00Z",
      revision_id: "revision-1",
    },
  ],
  queue: [
    {
      target_type: "idea_block",
      target_id: "block-2",
      target_label: "Settlement reconciles the ledger nightly.",
      target_kind: "Idea Block",
      provenance: ["Settlement runbook.md"],
      reason: "in a domain you read",
      viewed_at: "",
      revision_id: "revision-2",
    },
  ],
  permitted_domains: ["OSS"],
};

const NOTES = {
  notes: [
    {
      id: "pnote_1",
      target_type: "idea_block",
      target_id: "block-1",
      target_label: "The payment API writes settled payments to the ledger.",
      target_kind: "Idea Block",
      provenance: ["Payment architecture.md"],
      body: "Always the first question in reviews.",
      visibility: "private",
      domain_id: "OSS",
      created_at: "2026-10-27T09:05:00Z",
      updated_at: "2026-10-27T09:05:00Z",
    },
  ],
};

const SAVED = {
  saved_items: [
    {
      id: "saved_1",
      target_type: "idea_block",
      target_id: "block-1",
      target_label: "The payment API writes settled payments to the ledger.",
      target_kind: "Idea Block",
      label: "Read this again",
      tags: ["payments"],
      domain_id: "OSS",
      created_at: "2026-10-27T09:06:00Z",
    },
  ],
};

const THREADS = {
  threads: [
    {
      id: "thread_1",
      title: "Payment questions",
      domain_id: "OSS",
      created_at: "2026-10-27T09:10:00Z",
      updated_at: "2026-10-27T09:12:00Z",
    },
  ],
};

const EXPORT_BUNDLE = {
  export_id: "export_1",
  domain_id: "domain-a",
  state: "ready",
  content_hash: "abcdef0123456789",
  manifest: {
    format_version: "margin-export-v1",
    domain_id: "domain-a",
    generated_at: "2026-10-27T09:00:00Z",
    content_hash: "abcdef0123456789",
    record_kinds: { entities: 2, idea_block_revisions: 1 },
    included_record_kinds: ["entities", "idea_block_revisions"],
    excluded_by_construction: ["source file contents"],
  },
  error: null,
  created_at: "2026-10-27T09:00:00Z",
  completed_at: "2026-10-27T09:00:01Z",
};

const setupPersonalMockApi = async (
  page: Page
): Promise<{
  notes: Array<Record<string, unknown>>;
  saved: Array<Record<string, unknown>>;
  exports: string[];
  views: string[];
}> => {
  await page.addInitScript(() => {
    globalThis.localStorage.setItem("margin_access_token", "header.payload.signature");
    globalThis.localStorage.setItem("margin_user", "personal-smoke");
    globalThis.localStorage.setItem("margin_groups", "architect");
  });
  const observed = {
    notes: [] as Array<Record<string, unknown>>,
    saved: [] as Array<Record<string, unknown>>,
    exports: [] as string[],
    views: [] as string[],
  };

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();

    if (path === "/api/exports" && method === "POST") {
      observed.exports.push("POST /api/exports");
      await route.fulfill(json({ export_id: "export_1", state: "pending", domain_id: "domain-a" }));
      return;
    }
    if (path === "/api/exports/export_1" && method === "GET") {
      await route.fulfill(json(EXPORT_BUNDLE));
      return;
    }
    if (path === "/api/personal/views" && method === "POST") {
      const body = JSON.parse(request.postData() ?? "{}") as Record<string, unknown>;
      observed.views.push(`${body.target_type}:${body.target_id}`);
      await route.fulfill(json({ status: "recorded", target_id: body.target_id ?? "" }));
      return;
    }
    if (path === "/api/entities/entity-a") {
      await route.fulfill(
        json({
          id: "entity-a",
          preferred_label: "Payment API",
          ontology_type: "api",
          domain_id: "OSS",
          lifecycle_status: "active",
          definition: "Accepts payment requests.",
        })
      );
      return;
    }
    if (path === "/api/entities/catalog/ontology") {
      await route.fulfill(
        json({
          ontology_version: "margin-ontology-test",
          entity_types: ["api"],
          lifecycle_statuses: ["active"],
        })
      );
      return;
    }
    if (path === "/api/domains") {
      await route.fulfill(
        json({ domains: [{ id: "domain-a", name: "Domain A", status: "active" }] })
      );
      return;
    }
    if (path === "/api/personal/feed") {
      await route.fulfill(json(FEED));
      return;
    }
    if (path === "/api/personal/notes" && method === "GET") {
      await route.fulfill(json(NOTES));
      return;
    }
    if (path === "/api/personal/notes" && method === "PUT") {
      const body = JSON.parse(request.postData() ?? "{}") as Record<string, unknown>;
      observed.notes.push(body);
      await route.fulfill(json({ note: { ...NOTES.notes[0], ...body } }));
      return;
    }
    if (path === "/api/personal/saved" && method === "GET") {
      await route.fulfill(json(SAVED));
      return;
    }
    if (path === "/api/personal/saved" && method === "PUT") {
      const body = JSON.parse(request.postData() ?? "{}") as Record<string, unknown>;
      observed.saved.push(body);
      await route.fulfill(json({ saved_item: { ...SAVED.saved_items[0], ...body } }));
      return;
    }
    if (path === "/api/personal/threads") {
      await route.fulfill(json(THREADS));
      return;
    }
    if (path === "/api/personal/threads/thread_1") {
      await route.fulfill(
        json({
          thread: THREADS.threads[0],
          messages: [
            {
              id: "amsg_1",
              role: "user",
              content: "What writes to the ledger?",
              citation_ids: [],
              position: 0,
              created_at: "2026-10-27T09:10:00Z",
            },
            {
              id: "amsg_2",
              role: "assistant",
              content: "The payment API writes settled payments to the ledger.",
              citation_ids: ["idea_block:block-1"],
              position: 1,
              created_at: "2026-10-27T09:12:00Z",
            },
          ],
        })
      );
      return;
    }
    if (path === "/api/dashboard/summary") {
      await route.fulfill(
        json({
          review_tasks_assigned: 0,
          stale_claims: 0,
          expired_waivers: 0,
          recent_adrs: [],
          top_risks: [],
          top_debt: [],
        })
      );
      return;
    }
    await route.fulfill(json({}));
  });

  return observed;
};

test("memory surface: the home page opens on the reader's own space", async ({ page }) => {
  await setupPersonalMockApi(page);

  await page.goto("/");

  await expect(page.getByTestId("home-personal-feed")).toBeVisible();
  await expect(page.getByTestId("home-personal-entry")).toHaveCount(1);
  await expect(
    page.getByText("The payment API writes settled payments to the ledger.", { exact: true })
  ).toBeVisible();
  await expect(page.getByText("1 record in your reading queue.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Open your space" })).toBeVisible();
});

test("memory surface: your space shows notes, saved records and conversations", async ({
  page,
}) => {
  await setupPersonalMockApi(page);

  await page.goto("/me");

  await expect(page.getByTestId("personal-feed")).toBeVisible();
  await expect(page.getByTestId("personal-feed-entry")).toHaveCount(1);
  await expect(page.getByTestId("personal-provenance").first()).toContainText(
    "Payment architecture.md"
  );

  await expect(page.getByTestId("personal-queue")).toBeVisible();
  await expect(page.getByTestId("personal-queue-entry")).toHaveCount(1);
  await expect(page.getByText("Settlement reconciles the ledger nightly.")).toBeVisible();

  await expect(page.getByTestId("personal-note")).toHaveCount(1);
  await expect(page.getByText("Always the first question in reviews.")).toBeVisible();

  await expect(page.getByTestId("personal-saved-item")).toHaveCount(1);
  await expect(page.getByText("Read this again")).toBeVisible();

  await expect(page.getByTestId("personal-thread")).toHaveCount(1);
  await expect(page.getByText("Payment questions")).toBeVisible();
});

test("memory surface: a note written against a record is private and persists", async ({
  page,
}) => {
  const observed = await setupPersonalMockApi(page);

  await page.goto("/me");
  await page.getByRole("button", { name: "Write a note" }).click();
  await page.getByLabel(/Your note on/).fill("This is the part I always get asked about.");
  await page.getByRole("button", { name: "Save note" }).click();

  await expect.poll(() => observed.notes.length).toBe(1);
  expect(observed.notes[0]?.body).toBe("This is the part I always get asked about.");
  expect(observed.notes[0]?.target_id).toBe("block-1");
  expect(observed.notes[0]?.visibility).toBe("private");
});

test("memory surface: a saved record is kept once", async ({ page }) => {
  const observed = await setupPersonalMockApi(page);

  await page.goto("/me");
  await page.getByRole("button", { name: "Save", exact: true }).click();

  await expect.poll(() => observed.saved.length).toBe(1);
  expect(observed.saved[0]?.target_id).toBe("block-1");
});

test("memory surface: a conversation continues with its earlier turns", async ({ page }) => {
  await setupPersonalMockApi(page);

  await page.goto("/ask/thread_1");

  await expect(page.getByTestId("ask-thread-context")).toBeVisible();
  await expect(page.getByTestId("ask-thread-message")).toHaveCount(2);
  await expect(page.getByText("What writes to the ledger?")).toBeVisible();
  await expect(
    page.getByText("The payment API writes settled payments to the ledger.", { exact: true })
  ).toBeVisible();
});

test("memory surface: opening a record is remembered as recent activity", async ({ page }) => {
  const observed = await setupPersonalMockApi(page);

  await page.goto("/knowledge/entities/entity-a");

  await expect.poll(() => observed.views).toContain("entity:entity-a");
});

test("memory surface: an export is requested and its manifest is shown", async ({ page }) => {
  const observed = await setupPersonalMockApi(page);

  await page.goto("/me");
  await page.getByLabel("Domain to export").selectOption("domain-a");
  await page.getByRole("button", { name: "Request an export" }).click();

  await expect.poll(() => observed.exports.length).toBe(1);
  await expect(page.getByTestId("personal-export")).toBeVisible();
  await expect(page.getByTestId("personal-export-records")).toContainText("2 entities");
  await expect(page.getByTestId("personal-export-exclusions")).toContainText(
    "source file contents"
  );
});

import { expect, Page, test } from "@playwright/test";

const json = (payload: unknown) => ({
  body: JSON.stringify(payload),
  contentType: "application/json",
  status: 200,
});

const NARRATIVE = {
  domain_id: "OSS",
  title: "Operating Systems",
  markdown: [
    "# Operating Systems",
    "",
    "*Charter (v2, authored):* Core platform engineering and developer tooling.",
    "",
    "## What this domain covers",
    "",
    "### System (1)",
    "",
    "- **Payment API** — Accepts payment requests.",
    "",
    "## Verified claims",
    "",
    '- The payment API writes settled payments to the ledger. _("The payment API writes settled payments to the ledger.")_',
  ].join("\n"),
  truncated: false,
  charter_version: 2,
  entity_count: 1,
  claim_count: 1,
  decision_count: 0,
  references: [
    { kind: "domain", id: "OSS", label: "Operating Systems" },
    { kind: "entity", id: "ent-1", label: "Payment API" },
  ],
};

const KNOWN_UNKNOWNS = {
  domain_id: "OSS",
  known_unknowns: [
    {
      placeholder_id: "known_unknown_abc",
      domain_id: "OSS",
      text: "Settlement Gateway",
      normalized_text: "settlement gateway",
      proposed_type: "system",
      reference_count: 3,
      mention_ids: ["mention-1"],
    },
  ],
};

const setupReadingMockApi = async (
  page: Page
): Promise<{ authored: Array<Record<string, unknown>> }> => {
  await page.addInitScript(() => {
    globalThis.localStorage.setItem("margin_access_token", "header.payload.signature");
    globalThis.localStorage.setItem("margin_user", "reading-smoke");
    globalThis.localStorage.setItem("margin_groups", "architect");
  });
  const observed = { authored: [] as Array<Record<string, unknown>> };

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();

    if (path === "/api/domains/OSS/narrative") {
      await route.fulfill(json(NARRATIVE));
      return;
    }
    if (path === "/api/domains/OSS/known-unknowns") {
      await route.fulfill(json(KNOWN_UNKNOWNS));
      return;
    }
    if (path === "/api/domains/OSS/charter" && method === "GET") {
      await route.fulfill(
        json({
          domain_id: "OSS",
          active: {
            id: "charter_1",
            domain_id: "OSS",
            version: 2,
            status: "active",
            author_principal_id: "steward",
            purpose: "Core platform engineering and developer tooling.",
            scope_inclusions: "Operating systems and runtimes.",
            scope_exclusions: "Product analytics.",
            terminology: "OS means operating system.",
            created_at: "2026-10-01T00:00:00Z",
            activated_at: "2026-10-01T00:00:00Z",
            superseded_at: null,
          },
          history: [
            {
              id: "charter_2",
              domain_id: "OSS",
              version: 2,
              status: "active",
              author_principal_id: "steward",
              purpose: "Core platform engineering and developer tooling.",
              scope_inclusions: "",
              scope_exclusions: "",
              terminology: "",
              created_at: "2026-10-02T00:00:00Z",
              activated_at: "2026-10-02T00:00:00Z",
              superseded_at: null,
            },
            {
              id: "charter_1",
              domain_id: "OSS",
              version: 1,
              status: "superseded",
              author_principal_id: "steward",
              purpose: "Core platform engineering.",
              scope_inclusions: "",
              scope_exclusions: "",
              terminology: "",
              created_at: "2026-10-01T00:00:00Z",
              activated_at: "2026-10-01T00:00:00Z",
              superseded_at: "2026-10-02T00:00:00Z",
            },
          ],
        })
      );
      return;
    }
    if (path === "/api/domains/OSS/charter" && method === "PUT") {
      const body = JSON.parse(request.postData() ?? "{}") as Record<string, unknown>;
      observed.authored.push(body);
      await route.fulfill(
        json({
          charter: {
            id: "charter_3",
            domain_id: "OSS",
            version: 3,
            status: "active",
            author_principal_id: "reading-smoke",
            purpose: String(body.purpose ?? ""),
            scope_inclusions: "",
            scope_exclusions: "",
            terminology: "",
            created_at: "2026-10-03T00:00:00Z",
            activated_at: "2026-10-03T00:00:00Z",
            superseded_at: null,
          },
        })
      );
      return;
    }
    if (path === "/api/domains") {
      await route.fulfill(
        json({ domains: [{ id: "OSS", name: "Operating Systems", status: "active" }] })
      );
      return;
    }
    await route.fulfill(json({}));
  });

  return observed;
};

test("reading surface: the wiki leads with the domain narrative and its known unknowns", async ({
  page,
}) => {
  await setupReadingMockApi(page);

  await page.goto("/architecture/wiki");
  await page.getByRole("button", { name: "Generate architecture wiki" }).click();

  await expect(page.getByTestId("wiki-narrative")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Operating Systems" })).toBeVisible();
  await expect(page.getByText("Core platform engineering and developer tooling.")).toBeVisible();
  await expect(page.getByText("Payment API", { exact: true })).toBeVisible();

  await expect(page.getByTestId("wiki-known-unknowns")).toBeVisible();
  await expect(page.getByTestId("wiki-known-unknown")).toHaveCount(1);
  await expect(page.getByText("Settlement Gateway", { exact: true })).toBeVisible();
});

test("reading surface: a steward authors a new charter version", async ({ page }) => {
  const observed = await setupReadingMockApi(page);

  await page.goto("/knowledge/domains/OSS/charter");

  await expect(page.getByTestId("charter-history")).toBeVisible();
  await expect(page.getByTestId("charter-version")).toHaveCount(2);
  await expect(page.getByText("Version 2 is in force")).toBeVisible();

  await page
    .getByLabel("Purpose")
    .fill("Core platform engineering, developer tooling and support.");
  await page.getByRole("button", { name: "Save charter" }).click();

  await expect.poll(() => observed.authored.length).toBe(1);
  expect(observed.authored[0]?.purpose).toBe(
    "Core platform engineering, developer tooling and support."
  );
  expect(observed.authored[0]?.activate).toBe(true);
});

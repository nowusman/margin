import { expect, Page, test } from "@playwright/test";

const json = (payload: unknown) => ({
  body: JSON.stringify(payload),
  contentType: "application/json",
  status: 200,
});

const RUN = {
  id: "run_1",
  principal_id: "reader",
  domain_id: "domain-a",
  question: "Which records describe the payment flow?",
  state: "running",
  iteration_budget: 8,
  token_budget: 32000,
  iterations_used: 2,
  tokens_used: 1200,
  write_granted: false,
  answer: "",
  error: "",
  created_at: "2026-10-27T09:00:00Z",
  completed_at: null,
};

const DETAIL = {
  run: RUN,
  events: [
    {
      id: "runevt_1",
      sequence: 1,
      kind: "started",
      payload: { question: RUN.question },
      created_at: "2026-10-27T09:00:00Z",
    },
    {
      id: "runevt_2",
      sequence: 2,
      kind: "tool_call",
      payload: { tool: "knowledge_search" },
      created_at: "2026-10-27T09:00:02Z",
    },
  ],
  proposals: [
    {
      id: "proposal_1",
      run_id: "run_1",
      target_type: "sandbox_file",
      target_id: "note",
      path: "notes/note.md",
      summary: "Reword the note",
      diff: "-old\n+new",
      state: "proposed",
      reviewed_by: "",
      reviewed_at: null,
      created_at: "2026-10-27T09:00:03Z",
    },
  ],
  questions: [
    {
      id: "runq_1",
      request_key: "owner",
      title: "Which owner?",
      description: "The record has more than one candidate owner.",
      fields: [{ key: "owner", label: "Owner", type: "select", options: ["a", "b"], default: "" }],
      state: "pending",
      response: {},
    },
  ],
};

const setupAgentMockApi = async (page: Page): Promise<{ decisions: string[] }> => {
  await page.addInitScript(() => {
    globalThis.localStorage.setItem("margin_access_token", "header.payload.signature");
    globalThis.localStorage.setItem("margin_user", "agent-smoke");
    globalThis.localStorage.setItem("margin_groups", "architect");
  });
  const observed = { decisions: [] as string[] };

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();

    if (path === "/api/agent/runs" && method === "GET") {
      await route.fulfill(json({ runs: [RUN] }));
      return;
    }
    if (path === "/api/agent/runs" && method === "POST") {
      await route.fulfill(json({ run: RUN }));
      return;
    }
    if (path === "/api/agent/runs/run_1") {
      await route.fulfill(json(DETAIL));
      return;
    }
    if (path === "/api/agent/playbooks") {
      await route.fulfill(
        json({
          playbooks: [
            {
              name: "ledger",
              description: "How this domain is read.",
              version: "2",
              domain_id: "domain-a",
              applies_to: [],
              path: "playbooks/domain-a/ledger/playbook.json",
              supporting_files: ["rules.md"],
            },
          ],
          readable_domains: ["domain-a"],
        })
      );
      return;
    }
    if (path.startsWith("/api/agent/proposals/")) {
      observed.decisions.push(`${method} ${path}`);
      await route.fulfill(
        json({
          proposal: {
            ...DETAIL.proposals[0],
            state: method === "POST" && path.endsWith("approve") ? "applied" : "rejected",
          },
        })
      );
      return;
    }
    if (path.startsWith("/api/agent/requests/")) {
      observed.decisions.push(`${method} ${path}`);
      await route.fulfill(
        json({
          request: { ...DETAIL.questions[0], state: "answered", response: { owner: "a" } },
        })
      );
      return;
    }
    await route.fulfill(json({}));
  });

  return observed;
};

test("agent surface: a run shows its trail, its proposals and its questions", async ({ page }) => {
  await setupAgentMockApi(page);

  await page.goto("/agent/run_1");

  await expect(page.getByTestId("agent-run-detail")).toBeVisible();
  await expect(page.getByTestId("agent-event")).toHaveCount(2);
  await expect(page.getByText("tool_call")).toBeVisible();

  await expect(page.getByTestId("agent-proposal")).toHaveCount(1);
  await expect(page.getByTestId("agent-proposal-diff")).toContainText("+new");

  await expect(page.getByTestId("agent-questions")).toBeVisible();
  await expect(page.getByText("Which owner?")).toBeVisible();
});

test("agent surface: a proposed change is approved by a person", async ({ page }) => {
  const observed = await setupAgentMockApi(page);

  await page.goto("/agent/run_1");
  await page.getByRole("button", { name: "Approve" }).click();

  await expect.poll(() => observed.decisions.length).toBe(1);
  expect(observed.decisions[0]).toContain("/api/agent/proposals/proposal_1/approve");
});

test("agent surface: a question is answered by a person", async ({ page }) => {
  const observed = await setupAgentMockApi(page);

  await page.goto("/agent/run_1");
  await page.getByRole("button", { name: "Answer" }).click();

  await expect.poll(() => observed.decisions.length).toBe(1);
  expect(observed.decisions[0]).toContain("/api/agent/requests/runq_1/answer");
});

test("agent surface: the playbooks that govern a domain are listed", async ({ page }) => {
  await setupAgentMockApi(page);

  await page.goto("/agent");

  await expect(page.getByTestId("agent-playbook")).toHaveCount(1);
  await expect(page.getByText("How this domain is read.")).toBeVisible();
});

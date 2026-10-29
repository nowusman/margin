import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { adminClient, sourcesClient } from "./apiAdminSources";
import { dashboardClient } from "./apiDashboard";
import { governanceClient } from "./apiGovernance";
import { knowledgeClient } from "./apiKnowledge";
import { assistantClient } from "./apiRetrieval";

const jsonResponse = (payload: unknown): Response =>
  new Response(JSON.stringify(payload), {
    headers: { "Content-Type": "application/json" },
    status: 200,
  });

const setLocalStorage = (values: Record<string, string>): void => {
  const store = new Map<string, string>(Object.entries(values));
  Object.defineProperty(globalThis, "localStorage", {
    value: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => {
        store.set(key, value);
      },
      removeItem: (key: string) => {
        store.delete(key);
      },
    },
    configurable: true,
    writable: true,
  });
};

const responseFor = (input: RequestInfo | URL): Response => {
  const path = String(input);
  if (path === "/api/sources") {
    return jsonResponse({
      sources: [
        {
          id: "src-1",
          title: "OSS Eventing HLD",
          metadata: { upload_filename: "eventing-hld.md" },
          source_uri: "https://repo.example/eventing-hld.md",
        },
      ],
    });
  }
  if (path === "/api/dashboard/summary") {
    return jsonResponse({
      review_tasks_assigned: 1,
      stale_claims: 1,
      expired_waivers: 0,
      recent_adrs: [{ id: "adr-1", title: "Use Kafka", date: "2026-05-19" }],
      top_risks: [{ id: "risk-1", title: "Missing ADR", severity: "high" }],
      top_debt: [{ id: "debt-1", title: "Legacy bridge", atdi: 4 }],
    });
  }
  if (path === "/api/dashboard/heatmap") {
    return jsonResponse({
      domains: [
        { domain_id: "BSS", domain_name: "BSS", health: 90 },
        { domain_id: "OSS", domain_name: "OSS", health: 80 },
      ],
    });
  }
  if (path === "/api/dashboard/heatmap?domain_id=BSS") {
    return jsonResponse({
      domains: [{ domain_id: "BSS", domain_name: "BSS", health: 90 }],
    });
  }
  if (path === "/api/sources/src-1/pages") {
    return jsonResponse({ pages: [{ id: "page-1", page_number: 1 }] });
  }
  if (path === "/api/sources/src-1/elements") {
    return jsonResponse({ elements: [{ id: "elem-1", ordinal: 0, text: "content" }] });
  }
  if (path === "/api/governance/review/tasks") {
    return jsonResponse({
      tasks: [{ id: "rv-1", status: "open", reason: "Verify payment owner" }],
    });
  }
  if (path === "/api/governance/review/tasks/rv-1/action") {
    return jsonResponse({
      task: { id: "rv-1", status: "approved" },
      action: "confirm_equivalent",
      relation_id: "rel-1",
    });
  }
  if (path === "/api/jobs") {
    return jsonResponse({ jobs: [] });
  }
  if (path === "/api/entities") {
    return jsonResponse({ entities: [{ id: "ent-1", name: "Order API", kind: "system" }] });
  }
  if (path === "/api/domains?permission=read") {
    return jsonResponse({
      domains: [
        { id: "ALL", name: "All domains" },
        { id: "OSS", name: "OSS" },
        { id: "OSS", name: "OSS duplicate" },
        { id: "BSS", name: "BSS" },
      ],
    });
  }
  if (path === "/api/relationships/neighbors/ent-1") {
    return jsonResponse({
      entity_id: "ent-1",
      capped: false,
      edges: [
        {
          relationship_id: "rel-1",
          source: {
            id: "ent-1",
            label: "Order API",
            ontology_type: "system",
            domain_id: "OSS",
            lifecycle_status: "active",
          },
          target: {
            id: "ent-2",
            label: "Kafka",
            ontology_type: "technology",
            domain_id: "OSS",
            lifecycle_status: "active",
          },
          relation_type: "publishes_to",
          confidence: "verified",
          status: "verified",
          valid_from: "2026-01-01",
          valid_to: null,
          direction: "outbound",
          hop: 1,
          provenance: [
            {
              id: "prov-1",
              idea_block_id: "ib-1",
              source_element_id: "elem-1",
              source_quote: "Order API publishes events to Kafka.",
              polarity: "supports",
              status: "verified",
              valid_from: null,
              valid_to: null,
            },
          ],
        },
      ],
    });
  }
  if (path === "/api/relationships/path") {
    return jsonResponse({
      source_entity_id: "ent-1",
      target_entity_id: "ent-2",
      path: [],
      found: false,
      capped: false,
    });
  }
  if (path === "/api/relationships/impact/ent-1?max_depth=2&direction=both") {
    return jsonResponse({ entity_id: "ent-1", impacts: [], capped: false });
  }
  if (path === "/api/relationships/governed-entity/ent-1") {
    return jsonResponse({
      capped: false,
      governance_records: [
        {
          governance_record_id: "gov-1",
          business_key: "ADR-1",
          title: "Use Kafka",
          record_type: "adr",
          lifecycle_status: "accepted",
          domain_id: "OSS",
          role: "governed",
        },
      ],
    });
  }
  if (path === "/api/relationships/related-idea-block/ib-1") {
    return jsonResponse({ idea_block_id: "ib-1", relations: [], capped: false });
  }
  if (path === "/api/retrieval/filter-options") {
    return jsonResponse({
      ontology_version: "v1",
      categories: [{ id: "category-1", label: "Design Document" }],
      entity_types: ["system"],
      relationship_types: ["depends_on"],
      idea_block_types: ["statement"],
      idea_block_statuses: ["verified"],
      governance_record_types: ["adr"],
      governance_statuses: { adr: ["accepted"] },
      architecture_backlog_states: ["open"],
      sensitivities: ["internal"],
    });
  }
  if (path === "/api/ask" || path === "/api/search") {
    return jsonResponse({
      contract_version: "v1",
      response_mode: path === "/api/ask" ? "hybrid" : "lexical_only",
      results: [
        {
          idea_block_id: "ib-1",
          idea_block_revision_id: "ibr-1",
          score: 0.8,
          statement: "Kafka is supported by cited evidence.",
          type: "statement",
          status: "verified",
          polarity: "supports",
          entities: [{ id: "ent-1", name: "Kafka", type: "technology" }],
          citations: [
            {
              citation_id: "cit-1",
              idea_block_id: "ib-1",
              idea_block_revision_id: "ibr-1",
              source_document_id: "src-1",
              source_document_title: "ADR-004",
              source_element_id: "elem-1",
              quote: "Kafka is supported by cited evidence.",
              confidence: "high",
            },
          ],
          contradictions: [],
          supersession: [],
          governance_records: [
            { id: "gov-1", type: "adr", status: "accepted", title: "Use Kafka" },
          ],
        },
      ],
      applied_filters: { domain_ids: ["OSS"], statuses: ["verified"] },
      ambiguity: { entity_terms: [] },
      operational_warnings: path === "/api/search" ? ["Semantic retrieval unavailable"] : [],
      stage_timings: { lexical: 12 },
      answer: path === "/api/ask" ? "Kafka is supported by cited evidence. [cit-1]" : null,
      answer_citation_ids: path === "/api/ask" ? ["cit-1"] : [],
      answer_unavailable_reason: null,
      model: path === "/api/ask" ? { id: "model-1", name: "Answer model", version: "v1" } : null,
    });
  }
  if (path === "/api/evidence/ib-1") {
    return jsonResponse({ idea_block: { id: "ib-1" } });
  }
  if (path === "/api/admin/models") {
    return jsonResponse([
      {
        id: "model-1",
        displayName: "Primary Inference Provider",
        purpose: "extraction",
        runtime: "Ollama",
        modelName: "minimax-m3:cloud",
        endpointUrl: "http://host.docker.internal:11434/v1",
        secretConfigured: false,
        capabilities: {},
        contextLength: 4096,
        maxTokens: 24576,
        temperatureDefault: 0.1,
        license: "model-specific",
        hardwareProfile: "local-ollama",
        status: "active",
        defaultScopes: ["internal"],
      },
    ]);
  }
  return jsonResponse({});
};

describe("live api clients", () => {
  beforeEach(() => {
    setLocalStorage({});
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => Promise.resolve(responseFor(input)))
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("maps source, page and element API responses", async () => {
    const sources = await sourcesClient.list();
    expect(sources.length).toBeGreaterThan(0);
    expect(sources[0].title).toBe("eventing-hld.md");
    expect(sources[0].originalFilename).toBe("eventing-hld.md");
    expect(sources[0].pages).toEqual([]);
    expect(sources[0].elements).toEqual([]);

    const pages = await sourcesClient.pages(sources[0].id);
    const elements = await sourcesClient.elements(sources[0].id);

    expect(pages[0].id).toBe("page-1");
    expect(elements[0].id).toContain("elem");
  });

  it("filters pseudo and duplicate domain options", async () => {
    const domains = await knowledgeClient.listDomains("read");

    expect(domains.map((domain) => domain.name)).toEqual(["OSS", "BSS"]);
  });

  it("loads the enterprise heatmap without persisted domain scope by default", async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    fetchMock.mockClear();
    setLocalStorage({ margin_domain_scope: "BSS" });

    const domains = await dashboardClient.heatmap();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/dashboard/heatmap");
    expect(domains.map((domain) => domain.id)).toEqual(["BSS", "OSS"]);
  });

  it("can still load a scoped heatmap explicitly", async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    fetchMock.mockClear();
    setLocalStorage({ margin_domain_scope: "BSS" });

    const domains = await dashboardClient.heatmap({ scoped: true });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/dashboard/heatmap?domain_id=BSS");
    expect(domains.map((domain) => domain.id)).toEqual(["BSS"]);
  });

  it("uploads sources through multipart form data", async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    fetchMock.mockClear();

    await sourcesClient.upload(
      new globalThis.Blob(["# Design"], { type: "text/markdown" }),
      "design.md",
      {
        category: "Design Document",
        sensitivity: "internal",
        source_uri: "https://repo.example/design.md",
        source_type: "Markdown/TXT",
        extraction_mode: "deterministic",
      }
    );

    const [, init] = fetchMock.mock.calls[0];
    const body = init?.body as FormData;
    expect(fetchMock.mock.calls[0][0]).toBe("/api/sources");
    expect(init?.method).toBe("POST");
    expect(init?.headers).not.toHaveProperty("x-margin-groups");
    expect(init?.headers).not.toHaveProperty("x-margin-user");
    expect(init?.headers).not.toHaveProperty("Authorization");
    expect(body).toBeInstanceOf(globalThis.FormData);
    expect(init?.headers).not.toHaveProperty("Content-Type");
    expect(body.get("category")).toBe("Design Document");
    expect(body.get("sensitivity")).toBe("internal");
    expect(body.get("source_uri")).toBe("https://repo.example/design.md");
    expect(body.get("source_type")).toBe("Markdown/TXT");
    expect(body.get("extraction_mode")).toBe("deterministic");
  });

  it("does not send stale local identity headers after logout removes the access token", async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    fetchMock.mockClear();
    setLocalStorage({
      margin_user: "alice",
      margin_groups: "architect",
    });

    await sourcesClient.upload(
      new globalThis.Blob(["# Design"], { type: "text/markdown" }),
      "design.md",
      {
        category: "Design Document",
        sensitivity: "internal",
      }
    );

    const [, init] = fetchMock.mock.calls[0];

    expect(init?.headers).not.toHaveProperty("Authorization");
    expect(init?.headers).not.toHaveProperty("x-margin-user");
    expect(init?.headers).not.toHaveProperty("x-margin-groups");
  });

  it("keeps dev group headers when an access token is present", async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    fetchMock.mockClear();
    setLocalStorage({
      margin_access_token: "header.payload.signature",
      margin_user: "alice",
      margin_groups: "architect",
    });

    await sourcesClient.upload(
      new globalThis.Blob(["# Design"], { type: "text/markdown" }),
      "design.md",
      {
        category: "Design Document",
        sensitivity: "internal",
      }
    );

    const [, init] = fetchMock.mock.calls[0];

    expect(init?.headers).toMatchObject({
      Authorization: "Bearer header.payload.signature",
      "x-margin-user": "alice",
      "x-margin-groups": "architect",
    });
  });

  it("stores source URI as provenance metadata on uploaded files", async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    fetchMock.mockClear();

    await sourcesClient.upload(
      new globalThis.Blob(["# Overview"], { type: "text/markdown" }),
      "overview.md",
      {
        source_uri: "https://repo.example.org/architecture/overview.md",
        source_version: "2026-06-02",
      }
    );

    const [, init] = fetchMock.mock.calls[0];
    const body = init?.body as FormData;

    expect(fetchMock.mock.calls[0][0]).toBe("/api/sources");
    expect(body.get("file")).toBeInstanceOf(globalThis.File);
    expect(body.get("source_uri")).toBe("https://repo.example.org/architecture/overview.md");
    expect(body.get("source_type")).toBeNull();
    expect(body.get("source_version")).toBe("2026-06-02");
  });

  it("does not send the UI scope label as a source upload domain id", async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    fetchMock.mockClear();

    await sourcesClient.upload(
      new globalThis.Blob(["# Design"], { type: "text/markdown" }),
      "design.md",
      {
        domain_id: "  all domains  ",
        category: "Design Document",
      }
    );

    const [, init] = fetchMock.mock.calls[0];
    const body = init?.body as FormData;

    expect(body.get("domain_id")).toBeNull();
    expect(body.get("category")).toBe("Design Document");
  });

  it("maps dashboard summary and admin registry payloads", async () => {
    const [summary, models] = await Promise.all([dashboardClient.summary(), adminClient.models()]);

    expect(summary).toMatchObject({
      reviewTasksAssigned: 1,
      topRisks: [{ severity: "high" }],
      topDebt: [{ impact: "" }],
    });
    expect(models[0]).toMatchObject({ id: "model-1", defaultScopes: ["internal"] });
  });

  it("maps explicit relationship payloads and governance tasks", async () => {
    const [neighbors, path, impact, governed, related, tasks] = await Promise.all([
      knowledgeClient.relationshipNeighbors("ent-1"),
      knowledgeClient.relationshipPath({
        sourceEntityId: "ent-1",
        targetEntityId: "ent-2",
        maxDepth: 2,
      }),
      knowledgeClient.relationshipImpact("ent-1", { maxDepth: 2, direction: "both" }),
      knowledgeClient.governedEntityRelationships("ent-1"),
      knowledgeClient.relatedIdeaBlockRelationships("ib-1"),
      governanceClient.reviewTasks(),
    ]);
    expect(neighbors.edges[0]).toMatchObject({
      relationshipId: "rel-1",
      source: { label: "Order API" },
      provenance: [{ sourceQuote: "Order API publishes events to Kafka." }],
    });
    expect(path.found).toBe(false);
    expect(impact.capped).toBe(false);
    expect(governed.relationships[0]).toMatchObject({
      governanceRecordId: "gov-1",
      role: "governed",
    });
    expect(related.ideaBlockId).toBe("ib-1");
    expect(tasks[0]).toHaveProperty("status");
  });

  it("maps assistant provenance fields", async () => {
    const assistant = await assistantClient.ask({
      query: "why kafka",
      filters: {
        domainIds: [],
        categoryIds: [],
        entityIds: [],
        entityTerms: [],
        entityTypes: [],
        relationshipTypes: [],
        ideaBlockTypes: [],
        sourceDocumentIds: [],
        sensitivities: [],
        statuses: ["verified"],
        validFrom: null,
        validTo: null,
        governanceRecordTypes: [],
        governanceStatuses: [],
        architectureBacklogStates: [],
      },
      topK: 8,
      expand: { relationTypes: [], depth: 0, limit: 0 },
    });
    expect(assistant.results[0].citations[0]).toMatchObject({
      ideaBlockId: "ib-1",
      sourceTitle: expect.any(String),
      sourceElementId: "elem-1",
      confidence: expect.any(String),
      sourceVersionId: expect.any(String),
      governanceRecordId: expect.any(String),
    });
  });

  it("sends the v1 shared request shape and maps explicit response modes", async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    fetchMock.mockClear();

    const assistant = await assistantClient.ask({
      query: "why kafka",
      filters: {
        domainIds: ["OSS"],
        categoryIds: ["category-1"],
        entityIds: [],
        entityTerms: ["Kafka"],
        entityTypes: ["technology"],
        relationshipTypes: [],
        ideaBlockTypes: ["statement"],
        sourceDocumentIds: ["src-1"],
        sensitivities: ["internal"],
        statuses: ["verified"],
        validFrom: "2026-01-01",
        validTo: null,
        governanceRecordTypes: ["adr"],
        governanceStatuses: ["accepted"],
        architectureBacklogStates: [],
      },
      topK: 8,
      expand: { relationTypes: ["depends_on"], depth: 1, limit: 8 },
    });
    const requestBody = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));

    expect(requestBody).toMatchObject({
      query: "why kafka",
      filters: {
        domain_ids: ["OSS"],
        category_ids: ["category-1"],
        entity_terms: ["Kafka"],
        entity_types: ["technology"],
        source_document_ids: ["src-1"],
        sensitivities: ["internal"],
        statuses: ["verified"],
        governance_record_types: ["adr"],
        governance_statuses: ["accepted"],
      },
      top_k: 8,
      expand: { relation_types: ["depends_on"], depth: 1, limit: 8 },
    });
    expect(assistant).toMatchObject({
      contractVersion: "v1",
      responseMode: "hybrid",
      answerCitationIds: ["cit-1"],
    });

    const search = await assistantClient.search({
      query: "why kafka",
      filters: assistant.appliedFilters,
      topK: 8,
      expand: { relationTypes: [], depth: 0, limit: 0 },
    });
    expect(search).toMatchObject({
      responseMode: "lexical_only",
      operationalWarnings: [expect.any(String)],
    });
  });

  it("keeps production clients on live HTTP transport", () => {
    const sources = [
      "apiAdminSources.ts",
      "apiGovernance.ts",
      "apiKnowledge.ts",
      "apiRetrieval.ts",
    ].map((path) => readFileSync(new URL(`./${path}`, import.meta.url), "utf-8"));
    sources.forEach((source) => {
      expect(source).not.toContain("simulate(");
      expect(source).not.toContain("endpoint:");
    });
  });

  it("exposes executable methods on domain clients instead of a wrapper client", () => {
    expect(typeof sourcesClient.versions).toBe("function");
    expect(typeof sourcesClient.extractionReport).toBe("function");
    expect(typeof knowledgeClient.diagram).toBe("function");
    expect(typeof assistantClient.evidence).toBe("function");
  });

  it("uses the declared evidence endpoint for lookups", async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    fetchMock.mockClear();

    await assistantClient.evidence("ib-1");

    expect(fetchMock.mock.calls.map(([input]) => String(input))).toContain("/api/evidence/ib-1");
  });

  it("posts semantic review actions with the review bundle version", async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    fetchMock.mockClear();

    const result = await governanceClient.applyReviewAction("rv-1", {
      action: "confirm_equivalent",
      note: "The two statements describe the same decision.",
      expected_updated_at: "2026-09-08T05:00:00Z",
      survivor_id: "ib-1",
    });

    expect(result).toMatchObject({
      status: "approved",
      action: "confirm_equivalent",
      relationId: "rel-1",
    });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/governance/review/tasks/rv-1/action");
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      action: "confirm_equivalent",
      note: "The two statements describe the same decision.",
      expected_updated_at: "2026-09-08T05:00:00Z",
      survivor_id: "ib-1",
    });
  });
});

import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { buildPath, matchRoute } from "./lib/routes";
import { App } from "./main";

vi.mock("./lib/hooks", () => ({
  useLoad: (_key: string, loader: () => Promise<unknown>, options?: { enabled?: boolean }) => {
    if (options?.enabled !== false) {
      void loader().catch(() => undefined);
    }
    if (_key === "sources-list") {
      return {
        data: [
          {
            id: "src-123",
            title: "Architecture Overview",
            domainId: "Data & AI",
            category: "Design Document",
            sensitivity: "internal",
            status: "indexed",
            owner: "TSA",
            originalFilename: "overview.docx",
            sourceUri: "overview.docx",
            mediaType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            steward: "",
            createdAt: "2026-07-09T10:00:00Z",
            sections: [],
          },
        ],
        loading: false,
        error: null,
        refresh: () => undefined,
      };
    }
    if (_key === "source-jobs") {
      return {
        data: [
          {
            id: "job-1",
            sourceDocumentId: "src-123",
            sourceName: "Architecture Overview",
            state: "indexed",
            status: "indexed",
            stage: "indexed",
            attempts: 2,
            errorMessage: "",
            createdAt: "2026-07-09T11:00:00Z",
            updatedAt: "2026-07-09T11:05:00Z",
            startedAt: "2026-07-09T11:00:10Z",
            finishedAt: "2026-07-09T11:05:00Z",
            qualityReport: {},
            stageDetails: [],
          },
          {
            id: "job-2",
            sourceDocumentId: "src-123",
            sourceName: "Architecture Overview",
            state: "extraction_ready",
            status: "extraction_ready",
            stage: "extraction_ready",
            attempts: 1,
            errorMessage: "",
            createdAt: "2026-07-09T12:00:00Z",
            updatedAt: "2026-07-09T12:05:00Z",
            startedAt: "2026-07-09T12:00:10Z",
            finishedAt: "2026-07-09T12:05:00Z",
            qualityReport: {},
            stageDetails: [],
          },
        ],
        loading: false,
        error: null,
        refresh: () => undefined,
      };
    }
    if (_key === "source-filter-domains") {
      return {
        data: [{ id: "Data & AI", name: "Data & AI" }],
        loading: false,
        error: null,
        refresh: () => undefined,
      };
    }
    if (_key === "source-upload-domains") {
      return {
        data: [],
        loading: false,
        error: null,
        refresh: () => undefined,
      };
    }
    if (_key === "view-documents-sources") {
      return {
        data: [
          {
            id: "src-123",
            title: "Architecture Overview",
            mediaType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            sourceUri: "overview.docx",
            originalFilename: "overview.docx",
            sensitivity: "internal",
            status: "indexed",
          },
        ],
        loading: false,
        error: null,
        refresh: () => undefined,
      };
    }
    return { data: null, loading: false, error: null, refresh: () => undefined };
  },
  useCommand: () => ({ busy: false, message: null, runCommand: () => undefined }),
}));

const jsonResponse = (payload: unknown): Response =>
  new Response(JSON.stringify(payload), {
    headers: { "Content-Type": "application/json" },
    status: 200,
  });

const setPathname = (pathname: string): void => {
  Object.defineProperty(globalThis, "location", {
    value: { pathname },
    configurable: true,
    writable: true,
  });
};

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

const base64Url = (value: string): string =>
  globalThis.btoa(value).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");

const tokenWithPayload = (payload: Record<string, unknown>): string => {
  const header = base64Url(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = base64Url(JSON.stringify(payload));
  return `${header}.${body}.`;
};

describe("route params drive API endpoints", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({}))
    );
    setLocalStorage({});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses sourceId for source detail API requests", () => {
    setPathname("/sources/src-123");
    renderToStaticMarkup(<App />);
    const paths = vi.mocked(globalThis.fetch).mock.calls.map(([input]) => String(input));
    expect(paths).toContain("/api/sources/src-123");
  });

  it("uses the canonical source and element IDs for exact evidence", () => {
    setPathname("/sources/src-123/elements/element-7");
    renderToStaticMarkup(<App />);
    const paths = vi.mocked(globalThis.fetch).mock.calls.map(([input]) => String(input));

    expect(paths).toContain("/api/sources/src-123");
    expect(paths).toContain("/api/sources/src-123/elements");
  });

  it("renders the source table with action and job columns", () => {
    setPathname("/sources");
    const markup = renderToStaticMarkup(<App />);
    expect(markup).toContain("Action");
    expect(markup).toContain("View Doc");
    expect(markup).toContain('<option value="Data &amp; AI">Data &amp; AI</option>');
    expect(markup).toContain("No writable domains");
    expect(markup).toContain("State");
    expect(markup).toContain("Attempts");
    expect(markup).toContain('<td data-label="Attempts">3</td>');
    expect(markup).toContain("Select a source to view details.");
    expect(markup).not.toContain("Job history");
    expect(markup).not.toContain("Delete");
    expect(markup).not.toContain("Technical details");
  });

  it("renders selected source job history as a GMT table", () => {
    setPathname("/sources/src-123");
    const markup = renderToStaticMarkup(<App />);
    expect(markup).toContain("Job history");
    expect(markup).toContain("Jobs/retries: 3");
    expect(markup).toContain("Job ID");
    expect(markup).toContain("Created");
    expect(markup).toContain("Started");
    expect(markup).toContain("Finished");
    expect(markup).toContain("Error");
    expect(markup).toContain("job-2");
    expect(markup).toContain("2026-07-09 12:00:00 GMT");
    expect(markup).toContain("2026-07-09 12:00:10 GMT");
    expect(markup).not.toContain("Technical ID:");
    expect(markup).not.toContain("State: extraction_ready - Attempts");
  });

  it("renders source delete action for margin-admin users", () => {
    setPathname("/sources");
    setLocalStorage({
      margin_access_token: tokenWithPayload({ groups: ["margin-admin"] }),
    });
    const markup = renderToStaticMarkup(<App />);
    expect(markup).toContain("Delete");
  });

  it("uses sourceId for Office document preview API requests", () => {
    setPathname("/documents/src-123");
    renderToStaticMarkup(<App />);
    const paths = vi.mocked(globalThis.fetch).mock.calls.map(([input]) => String(input));
    expect(paths).toContain("/api/sources/src-123/preview-file");
  });

  it("renders not-found for removed ingestion job routes", () => {
    setPathname("/jobs/job-42");
    const markup = renderToStaticMarkup(<App />);
    const paths = vi.mocked(globalThis.fetch).mock.calls.map(([input]) => String(input));
    expect(paths).not.toContain("/api/jobs/job-42");
    expect(markup).toContain("Page not found");
  });

  it("uses entityId for knowledge entity detail API requests", () => {
    setPathname("/knowledge/entities/entity-7");
    renderToStaticMarkup(<App />);
    const paths = vi.mocked(globalThis.fetch).mock.calls.map(([input]) => String(input));
    expect(paths).toContain("/api/entities/entity-7");
  });

  it("uses canonical relationship endpoints for an entity relationship context", () => {
    setPathname("/knowledge/relationships/entity/entity-7");
    renderToStaticMarkup(<App />);
    const paths = vi.mocked(globalThis.fetch).mock.calls.map(([input]) => String(input));
    expect(paths.some((path) => path.startsWith("/api/relationships/neighbors/entity-7"))).toBe(
      true
    );
    expect(paths.some((path) => path.startsWith("/api/relationships/impact/entity-7"))).toBe(true);
    expect(paths).toContain("/api/relationships/governed-entity/entity-7?limit=100");
  });

  it("uses taskId for governance review detail API requests", () => {
    setPathname("/governance/review/review-88");
    setLocalStorage({ margin_access_token: tokenWithPayload({ groups: ["architect"] }) });
    renderToStaticMarkup(<App />);
    const paths = vi.mocked(globalThis.fetch).mock.calls.map(([input]) => String(input));
    expect(paths).toContain("/api/governance/review/tasks/review-88");
  });

  it("uses runId for evaluation run detail API requests", () => {
    setPathname("/quality/evaluations/evaluation-run-1");
    renderToStaticMarkup(<App />);
    const paths = vi.mocked(globalThis.fetch).mock.calls.map(([input]) => String(input));
    expect(paths).toContain("/api/evaluations/runs/evaluation-run-1");
  });

  it("renders not-found content for unknown routes", () => {
    setPathname("/does-not-exist");
    const markup = renderToStaticMarkup(<App />);
    expect(markup).toContain("Page not found");
  });

  it("guards governance routes for unauthorized roles", () => {
    setPathname("/governance/review");
    setLocalStorage({ margin_groups: "viewer" });
    const markup = renderToStaticMarkup(<App />);
    const paths = vi.mocked(globalThis.fetch).mock.calls.map(([input]) => String(input));
    expect(paths.some((path) => path.includes("/api/review/tasks"))).toBe(false);
    expect(markup).toContain("Access denied");
  });
});

describe("dynamic route matcher coverage", () => {
  const cases: Array<[string, string, Record<string, string>]> = [
    ["/sources/src-123", "/sources/:sourceId", { sourceId: "src-123" }],
    [
      "/sources/src-123/elements/element-7",
      "/sources/:sourceId/elements/:elementId",
      { sourceId: "src-123", elementId: "element-7" },
    ],
    ["/documents/src-123", "/documents/:sourceId", { sourceId: "src-123" }],
    ["/knowledge/domains/domain-oss", "/knowledge/domains/:domainId", { domainId: "domain-oss" }],
    ["/knowledge/entities/entity-7", "/knowledge/entities/:entityId", { entityId: "entity-7" }],
    ["/knowledge/ideablocks/ib-1", "/knowledge/ideablocks/:ideaBlockId", { ideaBlockId: "ib-1" }],
    [
      "/knowledge/relationships/entity/entity-7",
      "/knowledge/relationships/entity/:entityId",
      { entityId: "entity-7" },
    ],
    [
      "/knowledge/relationships/ideablock/ib-1",
      "/knowledge/relationships/ideablock/:ideaBlockId",
      { ideaBlockId: "ib-1" },
    ],
    [
      "/knowledge/relationships/governance/gov-1",
      "/knowledge/relationships/governance/:recordId",
      { recordId: "gov-1" },
    ],
    ["/governance/review/review-88", "/governance/review/:taskId", { taskId: "review-88" }],
    [
      "/quality/evaluations/evaluation-run-1",
      "/quality/evaluations/:runId",
      { runId: "evaluation-run-1" },
    ],
  ];

  it("parses every registered dynamic detail route", () => {
    cases.forEach(([path, expectedRoute, expectedParams]) => {
      expect(matchRoute(path)).toMatchObject({
        route: expectedRoute,
        params: expectedParams,
        path,
      });
    });
  });

  it("builds encoded paths for dynamic routes", () => {
    expect(buildPath("/knowledge/entities/:entityId", { entityId: "ADR 001" })).toBe(
      "/knowledge/entities/ADR%20001"
    );
  });
});

describe("registered route smoke coverage", () => {
  const paths = [
    "/",
    "/logged-out",
    "/ask",
    "/documents",
    "/documents/source-1",
    "/sources",
    "/sources/upload",
    "/sources/source-1",
    "/sources/source-1/elements/element-1",
    "/knowledge/domains",
    "/knowledge/domains/domain-1",
    "/knowledge/entities",
    "/knowledge/entities/entity-1",
    "/knowledge/ideablocks",
    "/knowledge/ideablocks/claim-1",
    "/knowledge/relationships",
    "/knowledge/relationships/entity/entity-1",
    "/knowledge/relationships/ideablock/claim-1",
    "/knowledge/relationships/governance/gov-1",
    "/views/heatmap",
    "/views/c4",
    "/views/c4/entity-1",
    "/governance/review",
    "/governance/review/task-1",
    "/quality/lint",
    "/quality/evaluations",
    "/quality/evaluations/run-1",
    "/admin/models",
    "/admin/access",
    "/admin/system",
  ];

  it("renders every application route without access or not-found regressions", () => {
    setLocalStorage({ margin_access_token: tokenWithPayload({ groups: ["platform_admin"] }) });
    paths.forEach((path) => {
      setPathname(path);
      const markup = renderToStaticMarkup(<App />);
      expect(markup, path).not.toContain("Page not found");
      expect(markup, path).not.toContain("Access denied");
    });
  });
});

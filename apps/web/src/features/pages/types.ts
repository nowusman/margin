export type AppRoute =
  | "/"
  | "/me"
  | "/agent"
  | "/agent/:runId"
  | "/not-found"
  | "/logged-out"
  | "/ask"
  | "/ask/:threadId"
  | "/documents"
  | "/documents/:sourceId"
  | "/sources"
  | "/sources/upload"
  | "/sources/:sourceId"
  | "/sources/:sourceId/elements/:elementId"
  | "/knowledge/domains"
  | "/knowledge/domains/:domainId"
  | "/knowledge/domains/:domainId/charter"
  | "/knowledge/entities"
  | "/knowledge/entities/:entityId"
  | "/knowledge/ideablocks"
  | "/knowledge/ideablocks/:ideaBlockId"
  | "/knowledge/relationships"
  | "/knowledge/relationships/entity/:entityId"
  | "/knowledge/relationships/ideablock/:ideaBlockId"
  | "/knowledge/relationships/governance/:recordId"
  | "/architecture/wiki"
  | "/views/graph"
  | "/views/graph/:entityId"
  | "/views/heatmap"
  | "/views/c4"
  | "/views/c4/:entityId"
  | "/governance"
  | "/governance/records/:recordId"
  | "/governance/review"
  | "/governance/review/:taskId"
  | "/quality/lint"
  | "/quality/evaluations"
  | "/quality/evaluations/:runId"
  | "/admin/models"
  | "/admin/access"
  | "/admin/system";

export type Role =
  | "Viewer"
  | "Architect"
  | "Domain Steward"
  | "Governance Admin"
  | "Platform Admin"
  | "Auditor";

export type NavItem = {
  label: string;
  route: AppRoute;
  roles?: Role[];
};

export type NavSection = {
  label: string;
  items: NavItem[];
};

export type RouteMeta = {
  title: string;
  description: string;
};

export const ROUTE_META: Record<AppRoute, RouteMeta> = {
  "/": {
    title: "Home",
    description: "Architecture knowledge state, review pressure, and operational visibility.",
  },
  "/not-found": {
    title: "Page Not Found",
    description: "The requested Margin screen does not exist or is no longer available.",
  },
  "/logged-out": { title: "Logged Out", description: "Session ended. Sign in again to continue." },
  "/ask": { title: "Ask Margin", description: "Cited Q&A with permitted evidence." },
  "/ask/:threadId": {
    title: "Ask Margin",
    description: "Cited Q&A with permitted evidence, continuing an earlier conversation.",
  },
  "/documents": {
    title: "View Documents",
    description: "Browse uploaded source documents and open converted content.",
  },
  "/documents/:sourceId": {
    title: "View Document",
    description: "Open one uploaded source document in the browser.",
  },
  "/sources": {
    title: "Sources",
    description: "Source artifact catalog with ingestion provenance.",
  },
  "/sources/upload": {
    title: "Knowledge Upload",
    description: "Upload architecture artifacts and extraction settings.",
  },
  "/sources/:sourceId": {
    title: "Source Detail",
    description: "Source metadata, reprocessing controls and extraction output.",
  },
  "/sources/:sourceId/elements/:elementId": {
    title: "Source Evidence",
    description: "Exact permitted source element and its extraction provenance.",
  },
  "/knowledge/domains": {
    title: "Domains",
    description: "Domain-level architecture entities and governance context.",
  },
  "/knowledge/domains/:domainId": {
    title: "Domain",
    description: "Domain overview, systems, governance links and history.",
  },
  "/knowledge/domains/:domainId/charter": {
    title: "Domain Charter",
    description: "Author what this domain covers so extraction reflects how the team works.",
  },
  "/knowledge/entities": {
    title: "Entities",
    description: "Entity explorer with relationship and evidence navigation.",
  },
  "/knowledge/entities/:entityId": {
    title: "Entity Detail",
    description: "Entity evidence, relationships, impact and linked governance records.",
  },
  "/knowledge/ideablocks": {
    title: "IdeaBlocks",
    description: "Candidate and verified architecture claims.",
  },
  "/knowledge/ideablocks/:ideaBlockId": {
    title: "IdeaBlock Detail",
    description: "Statement, source-element evidence, review and audit lifecycle.",
  },
  "/knowledge/relationships": {
    title: "Relationships",
    description: "Explicit entity relationships, bounded paths, and linked governance evidence.",
  },
  "/knowledge/relationships/entity/:entityId": {
    title: "Entity Relationships",
    description: "Direct relationships with bounded dependency and impact paths.",
  },
  "/knowledge/relationships/ideablock/:ideaBlockId": {
    title: "IdeaBlock Relationships",
    description: "Explicit semantic relationships and decision provenance for one IdeaBlock.",
  },
  "/knowledge/relationships/governance/:recordId": {
    title: "Governance Relationships",
    description: "Choose a governed entity to inspect its evidence-backed relationships.",
  },
  "/me": {
    title: "Your Space",
    description: "Your notes, saved records, reading queue and conversations.",
  },
  "/agent": {
    title: "The Agent",
    description: "Ask it to go and look. It reads and proposes; you decide.",
  },
  "/agent/:runId": {
    title: "Agent Run",
    description: "What the agent did, what it proposes, and what it still needs from you.",
  },
  "/architecture/wiki": {
    title: "Architecture Wiki",
    description: "Generate an authorized architecture snapshot from canonical records.",
  },
  "/views/graph": {
    title: "Knowledge Graph",
    description: "Explore the verified relationships you can read, by community and connection.",
  },
  "/views/graph/:entityId": {
    title: "Focused Knowledge Graph",
    description: "The verified neighbourhood of one record, with its justifying evidence.",
  },
  "/views/heatmap": {
    title: "Heatmap",
    description: "Domain health, staleness, risk, and debt overlays.",
  },
  "/views/c4": {
    title: "C4 Explorer",
    description: "System-to-component architecture navigation.",
  },
  "/views/c4/:entityId": {
    title: "C4 Focused View",
    description: "C4 explorer focused on a specific architecture entity.",
  },
  "/governance": {
    title: "Architecture Governance",
    description:
      "Decisions, risks, waivers, technical debt, and Architecture Backlog in one lifecycle workspace.",
  },
  "/governance/records/:recordId": {
    title: "Governance Record",
    description: "Record history, lifecycle, traceability, and evidence.",
  },
  "/governance/review": {
    title: "Review Queue",
    description: "Approvals for candidate or contradictory knowledge.",
  },
  "/governance/review/:taskId": {
    title: "Review Task Detail",
    description: "Evidence and decision workflow for one governance task.",
  },
  "/quality/lint": {
    title: "Lint Findings",
    description: "Architecture lint findings and corrective workflow.",
  },
  "/quality/evaluations": {
    title: "Evaluation Runs",
    description: "Quality and retrieval evaluation execution history.",
  },
  "/quality/evaluations/:runId": {
    title: "Evaluation Run Detail",
    description: "Metrics, thresholds and evidence for an evaluation run.",
  },
  "/admin/models": {
    title: "LLM & Models",
    description: "Provider configuration and health operations.",
  },
  "/admin/access": {
    title: "Users & Access",
    description: "Role, domain, sensitivity access administration.",
  },
  "/admin/system": {
    title: "System Health",
    description: "Platform and worker operational status.",
  },
};

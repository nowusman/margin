import { NavSection } from "../../features/pages/types";

export const NAVIGATION: NavSection[] = [
  {
    label: "Workspace",
    items: [
      { label: "Your Space", route: "/me" },
      { label: "The Agent", route: "/agent" },
      { label: "Sources", route: "/sources" },
      { label: "Knowledge", route: "/knowledge/ideablocks" },
      { label: "Ask Margin", route: "/ask" },
      { label: "Entities & Vocabulary", route: "/knowledge/entities" },
      {
        label: "Reviews",
        route: "/governance/review",
        roles: ["Architect", "Domain Steward", "Governance Admin", "Platform Admin", "Auditor"],
      },
      {
        label: "Architecture Governance",
        route: "/governance",
        roles: ["Architect", "Domain Steward", "Governance Admin", "Platform Admin", "Auditor"],
      },
      { label: "Architecture Wiki", route: "/architecture/wiki" },
      { label: "Knowledge Graph", route: "/views/graph" },
      { label: "Architecture Health", route: "/views/heatmap" },
      { label: "C4 Explorer", route: "/views/c4" },
    ],
  },
  {
    label: "Administration",
    items: [
      { label: "Models", route: "/admin/models", roles: ["Platform Admin"] },
      { label: "User Access", route: "/admin/access", roles: ["Platform Admin"] },
      { label: "System Health", route: "/admin/system", roles: ["Platform Admin", "Auditor"] },
    ],
  },
];

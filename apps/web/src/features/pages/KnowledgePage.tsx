import React from "react";
import { DomainKnowledgeView } from "./KnowledgeDomainView";
import { IdeaBlockKnowledgeView } from "./KnowledgeIdeaBlockView";
import { RelationshipKnowledgeView } from "./RelationshipKnowledgeView";
import { AppRoute } from "./types";

export type KnowledgePageProps = {
  route: AppRoute;
  params: Record<string, string>;
  onNavigate: (_route: AppRoute, _params?: Record<string, string>) => void;
};

export function KnowledgePage({
  route,
  params,
  onNavigate,
}: KnowledgePageProps): React.JSX.Element {
  if (route.startsWith("/knowledge/domains")) {
    return <DomainKnowledgeView params={params} onNavigate={onNavigate} />;
  }
  if (route.startsWith("/knowledge/ideablocks")) {
    return <IdeaBlockKnowledgeView params={params} onNavigate={onNavigate} />;
  }
  return <RelationshipKnowledgeView params={params} onNavigate={onNavigate} />;
}

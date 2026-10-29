import { apiClient } from "./api-client";
import { asArray, bool, num, rec, str } from "./apiMappers";
import type {
  DomainBriefing,
  DomainCharter,
  DomainCharterView,
  DomainNarrative,
  KnownUnknown,
} from "./apiTypes";

export type CharterInput = {
  purpose: string;
  scopeInclusions: string;
  scopeExclusions: string;
  terminology: string;
  activate: boolean;
};

const charter = (value: unknown): DomainCharter => {
  const item = rec(value);
  return {
    id: str(item.id),
    domainId: str(item.domain_id),
    version: num(item.version),
    status: str(item.status, "draft") as DomainCharter["status"],
    authorPrincipalId: str(item.author_principal_id),
    purpose: str(item.purpose),
    scopeInclusions: str(item.scope_inclusions),
    scopeExclusions: str(item.scope_exclusions),
    terminology: str(item.terminology),
    createdAt: str(item.created_at),
    activatedAt: str(item.activated_at),
    supersededAt: str(item.superseded_at),
  };
};

const knownUnknown = (value: unknown): KnownUnknown => {
  const item = rec(value);
  return {
    placeholderId: str(item.placeholder_id),
    domainId: str(item.domain_id),
    text: str(item.text),
    normalizedText: str(item.normalized_text),
    proposedType: str(item.proposed_type),
    referenceCount: num(item.reference_count),
    mentionIds: asArray<string>(item.mention_ids),
  };
};

export const domainReadingClient = {
  charter: (domainId: string): Promise<DomainCharterView> =>
    apiClient
      .get<unknown>(`/api/domains/${encodeURIComponent(domainId)}/charter`)
      .then((payload) => {
        const item = rec(payload);
        return {
          domainId: str(item.domain_id, domainId),
          active: item.active ? charter(item.active) : null,
          history: asArray<unknown>(item.history).map(charter),
        };
      }),

  authorCharter: (domainId: string, input: CharterInput): Promise<DomainCharter> =>
    apiClient
      .put<unknown>(`/api/domains/${encodeURIComponent(domainId)}/charter`, {
        purpose: input.purpose,
        scope_inclusions: input.scopeInclusions,
        scope_exclusions: input.scopeExclusions,
        terminology: input.terminology,
        activate: input.activate,
      })
      .then((payload: unknown) => charter(rec(payload).charter)),

  briefing: (domainId: string): Promise<DomainBriefing> =>
    apiClient
      .get<unknown>(`/api/domains/${encodeURIComponent(domainId)}/briefing`)
      .then((payload) => {
        const item = rec(payload);
        return {
          domainId: str(item.domain_id, domainId),
          briefing: str(item.briefing),
          truncated: bool(item.truncated),
          charterVersion: item.charter_version === null ? null : num(item.charter_version),
          entityCount: num(item.entity_count),
          ideaBlockCount: num(item.idea_block_count),
          governanceCounts: rec(item.governance_counts) as Record<string, number>,
        };
      }),

  narrative: (domainId: string): Promise<DomainNarrative> =>
    apiClient
      .get<unknown>(`/api/domains/${encodeURIComponent(domainId)}/narrative`)
      .then((payload) => {
        const item = rec(payload);
        return {
          domainId: str(item.domain_id, domainId),
          title: str(item.title, domainId),
          markdown: str(item.markdown),
          truncated: bool(item.truncated),
          charterVersion: item.charter_version === null ? null : num(item.charter_version),
          entityCount: num(item.entity_count),
          claimCount: num(item.claim_count),
          inferredClaimCount: num(item.inferred_claim_count),
          decisionCount: num(item.decision_count),
          references: asArray<unknown>(item.references).map((entry) => {
            const reference = rec(entry);
            return {
              kind: str(reference.kind),
              id: str(reference.id),
              label: str(reference.label),
            };
          }),
        };
      }),

  knownUnknowns: (domainId: string): Promise<KnownUnknown[]> =>
    apiClient
      .get<unknown>(`/api/domains/${encodeURIComponent(domainId)}/known-unknowns`)
      .then((payload) => asArray<unknown>(rec(payload).known_unknowns).map(knownUnknown)),
};

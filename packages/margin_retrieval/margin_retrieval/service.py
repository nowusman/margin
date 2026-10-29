"""One public service for authorized Margin Search and Ask results."""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import replace

from sqlalchemy.orm import Session

from margin_auth.permissions import PermissionSnapshot
from margin_db.charter import compose_domain_briefing
from margin_db.entity_catalog import resolve_entity_filter
from margin_db.personal import apply_personal_boost, personal_signals
from margin_db.user_models import Domain
from margin_embedding.interfaces import EmbeddingProvider
from margin_retrieval.adapters import AuthorizedRankingSet, RankedEvidence, validate_ranked_evidence
from margin_retrieval.answering import CatalogAnswerSynthesizer
from margin_retrieval.candidates import (
    DenseCandidateResult,
    FusedCandidate,
    dense_candidates,
    fuse_candidates,
    lexical_candidates,
    readable_idea_block_ids,
    source_discovery_candidates,
)
from margin_retrieval.context_budget import ContextBudget, resolve_context_budget
from margin_retrieval.expansion import expand_results
from margin_retrieval.hydration import hydrate_results, hydrate_source_discovery, revision_texts
from margin_retrieval.interfaces import (
    AnswerEnvelope,
    EvidenceItem,
    ExpansionEnvelope,
    RerankCandidate,
    RerankerProvider,
    RetrievalExpansion,
    RetrievalScope,
    SearchResult,
)
from margin_retrieval.metrics import record_stage_metric
from margin_retrieval.policy import (
    GOVERNANCE_RECORD_TYPES,
    RETRIEVAL_CANDIDATE_LIMIT,
    RETRIEVAL_RERANK_CANDIDATE_LIMIT,
    RetrievalPermissionError,
    ScopeAuthorizer,
    normalize_query,
    validate_expansion,
)
from margin_retrieval.support import is_postgresql, scope_dict

__all__ = ["RetrievalPermissionError", "RetrievalService"]


def _stage_metrics(metadata: dict[str, object]) -> dict[str, dict[str, object]]:
    """Return the structured stage metrics recorded during one retrieval."""

    raw_metrics = metadata.get("retrieval_stage_metrics")
    if not isinstance(raw_metrics, dict):
        raise RuntimeError("retrieval stage metrics must be a mapping")
    metrics: dict[str, dict[str, object]] = {}
    for stage, raw_values in raw_metrics.items():
        if not isinstance(stage, str) or not isinstance(raw_values, dict):
            raise RuntimeError("retrieval stage metrics must contain named mappings")
        values: dict[str, object] = {}
        for key, value in raw_values.items():
            if not isinstance(key, str):
                raise RuntimeError("retrieval stage metric keys must be strings")
            values[key] = value
        metrics[stage] = values
    return metrics


class RetrievalService:
    """Rank unique Idea Block revisions first and hydrate only final results."""

    DENSE_EMBEDDING_DEADLINE_MS = 250

    def __init__(
        self,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        reranker_provider: RerankerProvider | None = None,
        answer_synthesizer: CatalogAnswerSynthesizer | None = None,
    ) -> None:
        self._authorizer = ScopeAuthorizer()
        self._embedding_provider = embedding_provider
        self._reranker_provider = reranker_provider
        self._answer_synthesizer = answer_synthesizer or CatalogAnswerSynthesizer()

    def search(
        self,
        session: Session,
        *,
        query: str,
        scope: RetrievalScope,
        permissions: PermissionSnapshot,
        top_k: int = 8,
        expansion: RetrievalExpansion | None = None,
    ) -> SearchResult:
        validated_expansion = validate_expansion(expansion)
        authorized_scope = self._authorizer.authorize(session, permissions, scope)
        resolved_scope, ambiguity = self._resolve_entity_terms(session, authorized_scope)
        results, mode, metrics, warnings = self._retrieve(
            session,
            query=normalize_query(query),
            scope=resolved_scope,
            top_k=top_k,
            principal_id=permissions.principal_id,
        )
        expansion_result, expansion_warnings, expansion_metrics = self._expand(
            session, results=results, scope=resolved_scope, expansion=validated_expansion
        )
        return SearchResult(
            query=normalize_query(query),
            results=results,
            response_mode=mode,
            applied_scope=resolved_scope,
            stage_metrics={**metrics, **expansion_metrics},
            warnings=tuple(dict.fromkeys((*warnings, *expansion_warnings))),
            ambiguity=ambiguity,
            expansion=expansion_result,
        )

    def ask(
        self,
        session: Session,
        *,
        query: str,
        scope: RetrievalScope,
        permissions: PermissionSnapshot,
        top_k: int = 8,
        expansion: RetrievalExpansion | None = None,
        output_language: str = "",
        context_budget_chars: int | None = None,
        conversation_history: tuple[str, ...] = (),
    ) -> SearchResult:
        result = self.search(
            session, query=query, scope=scope, permissions=permissions, top_k=top_k, expansion=expansion
        )
        budget = resolve_context_budget(
            context_budget_chars, override=self._domain_context_budget(session, result.applied_scope)
        )
        briefing = self._compose_briefing(session, scope=result.applied_scope, budget=budget, permissions=permissions)
        synthesis = self._answer_synthesizer.synthesize(
            session,
            query=result.query,
            items=result.results,
            scope=result.applied_scope,
            briefing=briefing,
            output_language=output_language,
            conversation_history=conversation_history,
        )
        answer = AnswerEnvelope(
            status="generated" if synthesis.answer_markdown else "unavailable",
            markdown=synthesis.answer_markdown,
            unavailable_reason=synthesis.unavailable_reason,
            model=synthesis.model,
            latency_ms=synthesis.latency_ms,
            validation=synthesis.validation,
            citation_ids=synthesis.citation_ids,
        )
        warnings = result.warnings
        if synthesis.unavailable_reason:
            warnings = (*warnings, synthesis.unavailable_reason)
        metrics = {
            **result.stage_metrics,
            "answer_synthesis": {
                "count": 1,
                "latency_ms": synthesis.latency_ms,
                "validation": synthesis.validation,
                "mode": answer.status,
                "context_budget": budget.as_dict(),
                "briefing_chars": len(briefing),
            },
        }
        return SearchResult(
            query=result.query,
            results=result.results,
            response_mode="search_only" if answer.status == "unavailable" else result.response_mode,
            applied_scope=result.applied_scope,
            stage_metrics=metrics,
            warnings=warnings,
            ambiguity=result.ambiguity,
            answer=answer,
            expansion=result.expansion,
        )

    def _domain_context_budget(self, session: Session, scope: RetrievalScope) -> int | None:
        """Return the context budget the domain in scope has configured.

        A question that spans several domains uses the global configuration,
        because there is no single domain whose setting would be the right one.
        """

        if len(scope.domain_ids) != 1:
            return None
        domain = session.get(Domain, scope.domain_ids[0])
        if domain is None:
            return None
        return domain.context_budget_chars

    def _apply_personal_boost(
        self,
        session: Session,
        *,
        candidates: tuple[FusedCandidate, ...],
        principal_id: str,
        scope: RetrievalScope,
        metadata: dict[str, object],
    ) -> tuple[FusedCandidate, ...]:
        """Raise records this reader has engaged with, within a narrow band.

        The boost runs after fusion and before reranking, and it can only
        reorder candidates that are already close to the strongest match. It
        never introduces a record the candidate query did not return, and it
        never lets a personal favourite outrank a substantially better match.
        """

        if not principal_id or principal_id == "anonymous" or not candidates:
            return candidates
        signals = personal_signals(
            session,
            owner_principal_id=principal_id,
            revision_ids=tuple(candidate.revision_id for candidate in candidates),
        )
        if not signals:
            return candidates
        base = tuple((candidate.revision_id, candidate.score) for candidate in candidates)
        boost = apply_personal_boost(base, signals)
        score_by_revision = dict(boost.candidates)
        order_by_revision = {revision_id: index for index, (revision_id, _) in enumerate(boost.candidates)}
        reordered = tuple(
            sorted(
                candidates, key=lambda candidate: order_by_revision.get(candidate.revision_id, len(order_by_revision))
            )
        )
        record_stage_metric(
            metadata,
            "personal_boost",
            count=len(signals),
            latency_ms=0,
            metadata={
                "signals": {revision_id: list(signal.reasons) for revision_id, signal in sorted(signals.items())},
                "order_changed": boost.reordered,
                "largest_position_shift": boost.largest_position_shift,
                "largest_score_change": round(boost.largest_score_change, 6),
            },
        )
        return tuple(
            replace(candidate, score=score_by_revision.get(candidate.revision_id, candidate.score))
            for candidate in reordered
        )

    def _compose_briefing(
        self, session: Session, *, scope: RetrievalScope, budget: ContextBudget, permissions: PermissionSnapshot
    ) -> str:
        """Compose the domain background an answer is read against.

        The briefing describes the domain, not the answer. It is bounded by the
        index share of the context budget, because it is context rather than
        evidence, and because a domain with many sub-domains must not crowd out
        the records themselves.
        """

        domain_ids = tuple(scope.domain_ids)
        if not domain_ids:
            return ""
        readable_blocks = readable_idea_block_ids(session, scope)
        permitted_governance = frozenset(
            record_type
            for record_type in GOVERNANCE_RECORD_TYPES
            if permissions.allows_category(f"governance:{record_type}", "read")
        )
        sections: list[str] = []
        remaining = budget.index_chars
        for domain_id in domain_ids:
            if remaining <= 0:
                break
            composed = compose_domain_briefing(
                session,
                domain_id,
                readable_idea_block_ids=readable_blocks,
                permitted_governance_record_types=permitted_governance,
                max_chars=remaining,
            )
            briefing = str(composed["briefing"])
            if not briefing:
                continue
            sections.append(briefing)
            remaining -= len(briefing)
        return "\n\n".join(sections)

    def discover_sources(
        self, session: Session, *, query: str, scope: RetrievalScope, permissions: PermissionSnapshot, top_k: int = 8
    ) -> SearchResult:
        """Run explicit extraction-gap discovery without competing with Idea Block search."""

        authorized_scope = self._authorizer.authorize(session, permissions, scope)
        resolved_scope, ambiguity = self._resolve_entity_terms(session, authorized_scope)
        started = time.perf_counter()
        candidates = source_discovery_candidates(
            session,
            query_text=normalize_query(query),
            scope=resolved_scope,
            limit=min(RETRIEVAL_CANDIDATE_LIMIT, top_k),
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return SearchResult(
            query=normalize_query(query),
            results=hydrate_source_discovery(session, candidates=candidates[:top_k], scope=resolved_scope),
            response_mode="lexical_only",
            applied_scope=resolved_scope,
            stage_metrics={
                "source_discovery": {"count": len(candidates), "latency_ms": elapsed_ms, "mode": "explicit"}
            },
            warnings=("postgres_unavailable",) if not is_postgresql(session) else (),
            ambiguity=ambiguity,
        )

    @staticmethod
    def _expand(
        session: Session, *, results: tuple[EvidenceItem, ...], scope: RetrievalScope, expansion: RetrievalExpansion
    ) -> tuple[ExpansionEnvelope, tuple[str, ...], dict[str, dict[str, object]]]:
        started = time.perf_counter()
        seeds = tuple(
            str(item.metadata["idea_block_id"])
            for item in results
            if isinstance(item.metadata.get("idea_block_id"), str)
        )
        candidates, envelope = expand_results(session, seed_block_ids=seeds, scope=scope, expansion=expansion)
        expanded = hydrate_results(session, candidates=candidates, scope=scope)
        envelope = replace(envelope, results=expanded)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        warning = ("expansion_deadline_exceeded",) if envelope.status == "deadline_exceeded" else ()
        return (
            envelope,
            warning,
            {"post_retrieval_expansion": {"count": len(expanded), "latency_ms": elapsed_ms, "mode": envelope.status}},
        )

    @staticmethod
    def _resolve_entity_terms(
        session: Session, scope: RetrievalScope
    ) -> tuple[RetrievalScope, tuple[dict[str, object], ...]]:
        entity_ids = list(scope.entity_ids)
        ambiguity: list[dict[str, object]] = []
        for term in scope.entity_terms:
            resolution = resolve_entity_filter(
                session, text=term, domain_ids=scope.domain_ids, ontology_types=scope.entity_types
            )
            if resolution.entity_id:
                entity_ids.append(resolution.entity_id)
                continue
            ambiguity.append(
                {
                    "term": term,
                    "status": "ambiguous" if resolution.is_ambiguous else "unresolved",
                    "candidates": list(resolution.candidates),
                }
            )
        return replace(scope, entity_ids=tuple(dict.fromkeys(entity_ids))), tuple(ambiguity)

    def _retrieve(
        self, session: Session, *, query: str, scope: RetrievalScope, top_k: int, principal_id: str = ""
    ) -> tuple[tuple[EvidenceItem, ...], str, dict[str, dict[str, object]], tuple[str, ...]]:
        limit = min(RETRIEVAL_CANDIDATE_LIMIT, max(top_k, top_k * 4))
        metadata: dict[str, object] = {"authorized_scope": scope_dict(scope), "retrieval_stage_metrics": {}}
        warnings: list[str] = []
        lexical_started = time.perf_counter()
        lexical = lexical_candidates(session, query_text=query, scope=scope, limit=limit)
        lexical_mode = "healthy" if is_postgresql(session) else "postgres_unavailable"
        record_stage_metric(
            metadata,
            "lexical_candidates",
            count=len(lexical),
            latency_ms=int((time.perf_counter() - lexical_started) * 1000),
            metadata={"mode": lexical_mode},
        )
        if lexical_mode != "healthy":
            warnings.append(lexical_mode)

        dense_started = time.perf_counter()
        dense = dense_candidates(
            session,
            query_text=query,
            scope=scope,
            embedding_provider=self._embedding_provider,
            deadline_ms=self.DENSE_EMBEDDING_DEADLINE_MS,
            limit=limit,
        )
        self._record_dense_metric(metadata, dense, started=dense_started)
        if dense.mode not in {"healthy", "not_configured"}:
            warnings.append("dense_unavailable")

        fusion_started = time.perf_counter()
        fused = fuse_candidates((lexical, dense.candidates), limit=limit)
        retrieval_mode = "hybrid" if lexical and dense.candidates else "lexical_only"
        channel_counts: Counter[str] = Counter()
        for candidate in fused:
            for channel in candidate.channel_ranks:
                channel_counts[channel] += 1
        record_stage_metric(
            metadata,
            "rrf_fusion",
            count=len(fused),
            latency_ms=int((time.perf_counter() - fusion_started) * 1000),
            metadata={"mode": retrieval_mode, "channels": dict(sorted(channel_counts.items()))},
        )
        boosted = self._apply_personal_boost(
            session, candidates=fused, principal_id=principal_id, scope=scope, metadata=metadata
        )
        reranked = self._rerank(
            session, query=query, scope=scope, candidates=boosted, limit=top_k, metadata=metadata, warnings=warnings
        )
        hydration_started = time.perf_counter()
        results = hydrate_results(session, candidates=reranked[:top_k], scope=scope)
        record_stage_metric(
            metadata,
            "result_hydration",
            count=len(results),
            latency_ms=int((time.perf_counter() - hydration_started) * 1000),
        )
        metrics = _stage_metrics(metadata)
        return results, retrieval_mode, metrics, tuple(dict.fromkeys(warnings))

    def _record_dense_metric(self, metadata: dict[str, object], dense: DenseCandidateResult, *, started: float) -> None:
        record_stage_metric(
            metadata,
            "dense_candidates",
            count=len(dense.candidates),
            latency_ms=int((time.perf_counter() - started) * 1000),
            metadata={"mode": dense.mode},
        )

    def _rerank(
        self,
        session: Session,
        *,
        query: str,
        scope: RetrievalScope,
        candidates: tuple[FusedCandidate, ...],
        limit: int,
        metadata: dict[str, object],
        warnings: list[str],
    ) -> tuple[FusedCandidate, ...]:
        started = time.perf_counter()
        bounded = candidates[:RETRIEVAL_RERANK_CANDIDATE_LIMIT]
        if self._reranker_provider is None or not bounded:
            record_stage_metric(
                metadata,
                "rerank",
                count=len(bounded),
                latency_ms=int((time.perf_counter() - started) * 1000),
                metadata={"mode": "not_configured" if self._reranker_provider is None else "empty"},
            )
            return bounded[:limit]
        texts = revision_texts(session, (candidate.revision_id for candidate in bounded))
        authorized = tuple(candidate for candidate in bounded if candidate.revision_id in texts)
        ranking_set = AuthorizedRankingSet(
            query=query, scope=scope, candidate_evidence_ids=tuple(candidate.revision_id for candidate in authorized)
        )
        try:
            ranked = self._reranker_provider.rerank(
                query=query,
                scope=scope,
                candidates=tuple(
                    RerankCandidate(
                        evidence_id=candidate.revision_id,
                        stage="rrf",
                        text=texts[candidate.revision_id],
                        base_score=candidate.score,
                    )
                    for candidate in authorized
                ),
                limit=min(limit, len(authorized)),
            )
            validate_ranked_evidence(
                ranking_set, tuple(RankedEvidence(evidence_id=item.evidence_id, score=item.score) for item in ranked)
            )
        except (RuntimeError, ValueError, TimeoutError, OSError):
            warnings.append("rerank_unavailable")
            record_stage_metric(
                metadata,
                "rerank",
                count=len(authorized),
                latency_ms=int((time.perf_counter() - started) * 1000),
                metadata={"mode": "unhealthy"},
            )
            return bounded[:limit]
        candidates_by_id = {candidate.revision_id: candidate for candidate in authorized}
        reranked = tuple(
            FusedCandidate(
                revision_id=item.evidence_id,
                score=item.score,
                channel_ranks={**candidates_by_id[item.evidence_id].channel_ranks, "rerank": rank},
            )
            for rank, item in enumerate(ranked, start=1)
            if item.evidence_id in candidates_by_id
        )
        record_stage_metric(
            metadata,
            "rerank",
            count=len(reranked),
            latency_ms=int((time.perf_counter() - started) * 1000),
            metadata={"mode": "healthy"},
        )
        return reranked[:limit]

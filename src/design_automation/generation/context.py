"""Build a stable grounded context from retrieval and graph outputs."""

from collections.abc import Sequence

from design_automation.core import DEFAULT_CONTEXT_DOCUMENT_LIMIT
from design_automation.models import (
    DocumentEvidence,
    GraphContext,
    GraphEvidence,
    GroundedContext,
    RetrievalResult,
)


class ContextBuilder:
    """Convert infrastructure-facing results into generation-facing evidence."""

    def __init__(
        self,
        max_document_chunks: int = DEFAULT_CONTEXT_DOCUMENT_LIMIT,
    ) -> None:
        if max_document_chunks <= 0:
            raise ValueError("max_document_chunks must be positive")
        self._max_document_chunks = max_document_chunks

    def build(
        self,
        requirement: str,
        requested_section: str,
        retrieval_results: Sequence[RetrievalResult],
        graph_context: GraphContext,
    ) -> GroundedContext:
        if not requirement.strip():
            raise ValueError("requirement cannot be empty")
        if not requested_section.strip():
            raise ValueError("requested_section cannot be empty")

        document_evidence = [
            self._document_evidence(result, rank)
            for rank, result in enumerate(
                retrieval_results[: self._max_document_chunks],
                start=1,
            )
        ]
        graph_evidence = self._graph_evidence(graph_context)

        provenance: list[str] = []
        seen_sources: set[str] = set()
        for evidence in document_evidence:
            if evidence.source_path not in seen_sources:
                seen_sources.add(evidence.source_path)
                provenance.append(evidence.source_path)

        return GroundedContext(
            requirement=requirement.strip(),
            requested_section=requested_section.strip(),
            document_evidence=document_evidence,
            graph_evidence=graph_evidence,
            source_provenance=provenance,
        )

    @staticmethod
    def _document_evidence(
        result: RetrievalResult,
        fallback_rank: int,
    ) -> DocumentEvidence:
        title = str(result.metadata.get("title") or result.document_id)
        source_path = str(result.metadata.get("source_path") or result.source)
        return DocumentEvidence(
            chunk_id=result.chunk_id,
            document_id=result.document_id,
            document_title=title,
            source_path=source_path,
            section=result.section,
            text=result.text,
            retrieval_rank=result.rank or fallback_rank,
            fusion_score=result.fusion_score,
            metadata=dict(result.metadata),
        )

    @staticmethod
    def _graph_evidence(graph_context: GraphContext) -> list[GraphEvidence]:
        evidence_by_identity: dict[
            tuple[str, str, str, str, str],
            GraphEvidence,
        ] = {}
        for relationship in graph_context.relationships:
            evidence = GraphEvidence(
                source_entity=relationship.source_name,
                source_type=relationship.source_type,
                relationship=relationship.relationship_type,
                target_entity=relationship.target_name,
                target_type=relationship.target_type,
            )
            evidence_by_identity[evidence.identity] = evidence
        return [
            evidence_by_identity[identity]
            for identity in sorted(evidence_by_identity)
        ]

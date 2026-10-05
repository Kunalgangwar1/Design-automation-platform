"""Rank-based fusion for dense and lexical candidates."""

from design_automation.models import RetrievalOrigin, RetrievalProvider, RetrievalResult


def reciprocal_rank_fusion(
    dense_results: list[RetrievalResult],
    lexical_results: list[RetrievalResult],
    top_k: int,
    rrf_constant: int,
) -> list[RetrievalResult]:
    """Fuse ranks without mixing incomparable cosine and BM25 score scales."""

    if top_k <= 0:
        raise ValueError("top_k must be positive")
    if rrf_constant < 0:
        raise ValueError("rrf_constant cannot be negative")

    candidates: dict[str, dict[str, object]] = {}
    for rank, result in enumerate(dense_results, start=1):
        candidates[result.chunk_id] = {
            "base": result,
            "dense_rank": rank,
            "dense_score": result.dense_score,
            "lexical_rank": None,
            "lexical_score": None,
            "fusion_score": 1.0 / (rrf_constant + rank),
        }

    for rank, result in enumerate(lexical_results, start=1):
        candidate = candidates.setdefault(
            result.chunk_id,
            {
                "base": result,
                "dense_rank": None,
                "dense_score": None,
                "lexical_rank": None,
                "lexical_score": None,
                "fusion_score": 0.0,
            },
        )
        candidate["lexical_rank"] = rank
        candidate["lexical_score"] = result.lexical_score
        candidate["fusion_score"] = float(candidate["fusion_score"]) + (
            1.0 / (rrf_constant + rank)
        )

    ordered = sorted(
        candidates.values(),
        key=lambda item: (
            -float(item["fusion_score"]),
            min(
                rank
                for rank in (item["dense_rank"], item["lexical_rank"])
                if isinstance(rank, int)
            ),
            str(item["base"].chunk_id),
        ),
    )

    fused: list[RetrievalResult] = []
    for rank, item in enumerate(ordered[:top_k], start=1):
        base = item["base"]
        if not isinstance(base, RetrievalResult):
            raise TypeError("RRF candidate has an invalid result")
        dense_rank = item["dense_rank"]
        lexical_rank = item["lexical_rank"]
        origin = (
            RetrievalOrigin.BOTH
            if dense_rank is not None and lexical_rank is not None
            else RetrievalOrigin.DENSE
            if dense_rank is not None
            else RetrievalOrigin.LEXICAL
        )
        dense_score = item["dense_score"]
        fused.append(
            RetrievalResult(
                chunk_id=base.chunk_id,
                document_id=base.document_id,
                section=base.section,
                source=base.source,
                text=base.text,
                similarity_score=(
                    float(dense_score) if dense_score is not None else None
                ),
                metadata=dict(base.metadata),
                provider=RetrievalProvider.RRF,
                rank=rank,
                provider_score=float(item["fusion_score"]),
                dense_rank=int(dense_rank) if dense_rank is not None else None,
                dense_score=(
                    float(dense_score) if dense_score is not None else None
                ),
                lexical_rank=(
                    int(lexical_rank) if lexical_rank is not None else None
                ),
                lexical_score=(
                    float(item["lexical_score"])
                    if item["lexical_score"] is not None
                    else None
                ),
                fusion_score=float(item["fusion_score"]),
                origin=origin,
            )
        )
    return fused

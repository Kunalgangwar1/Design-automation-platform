"""Deterministic information-retrieval metrics over ranked chunk IDs."""

from __future__ import annotations

from collections.abc import Iterable, Sequence


def unique_ranking(retrieved_chunk_ids: Sequence[str]) -> list[str]:
    """Remove duplicate IDs while preserving their first observed rank."""

    seen: set[str] = set()
    ranking: list[str] = []
    for chunk_id in retrieved_chunk_ids:
        if chunk_id not in seen:
            seen.add(chunk_id)
            ranking.append(chunk_id)
    return ranking


def _validate_k(k: int) -> None:
    if k <= 0:
        raise ValueError("k must be positive")


def precision_at_k(
    retrieved_chunk_ids: Sequence[str],
    relevant_chunk_ids: Iterable[str],
    k: int,
) -> float:
    """Relevant unique results in the first K divided by K."""

    _validate_k(k)
    relevant = set(relevant_chunk_ids)
    hits = len(set(unique_ranking(retrieved_chunk_ids)[:k]) & relevant)
    return hits / k


def recall_at_k(
    retrieved_chunk_ids: Sequence[str],
    relevant_chunk_ids: Iterable[str],
    k: int,
) -> float:
    """Relevant unique results in the first K divided by known relevant items."""

    _validate_k(k)
    relevant = set(relevant_chunk_ids)
    if not relevant:
        return 0.0
    hits = len(set(unique_ranking(retrieved_chunk_ids)[:k]) & relevant)
    return hits / len(relevant)


def hit_at_k(
    retrieved_chunk_ids: Sequence[str],
    relevant_chunk_ids: Iterable[str],
    k: int,
) -> float:
    """Return one when any known relevant item occurs in the first K."""

    _validate_k(k)
    relevant = set(relevant_chunk_ids)
    return float(bool(set(unique_ranking(retrieved_chunk_ids)[:k]) & relevant))


def first_relevant_rank(
    retrieved_chunk_ids: Sequence[str],
    relevant_chunk_ids: Iterable[str],
    max_k: int | None = None,
) -> int | None:
    relevant = set(relevant_chunk_ids)
    ranking = unique_ranking(retrieved_chunk_ids)
    if max_k is not None:
        _validate_k(max_k)
        ranking = ranking[:max_k]
    for rank, chunk_id in enumerate(ranking, start=1):
        if chunk_id in relevant:
            return rank
    return None


def reciprocal_rank(
    retrieved_chunk_ids: Sequence[str],
    relevant_chunk_ids: Iterable[str],
    max_k: int | None = None,
) -> float:
    """Return inverse rank of the first relevant result, or zero."""

    rank = first_relevant_rank(retrieved_chunk_ids, relevant_chunk_ids, max_k)
    return 1.0 / rank if rank is not None else 0.0


def mean_reciprocal_rank(
    rankings: Sequence[tuple[Sequence[str], Iterable[str]]],
    max_k: int | None = None,
) -> float:
    """Return the arithmetic mean reciprocal rank across query cases."""

    if not rankings:
        return 0.0
    return sum(
        reciprocal_rank(retrieved, relevant, max_k)
        for retrieved, relevant in rankings
    ) / len(rankings)

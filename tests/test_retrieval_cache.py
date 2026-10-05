"""Offline tests for the process-local retrieval response cache."""

from design_automation.models import RetrievalMode, RetrievalResponse
from design_automation.retrieval.cache import (
    CachedRetrievalCoordinator,
    RetrievalCache,
)


class StubCoordinator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, RetrievalMode | str, int]] = []

    def retrieve(
        self,
        query: str,
        mode: RetrievalMode | str,
        top_k: int,
    ) -> RetrievalResponse:
        self.calls.append((query, mode, top_k))
        selected = RetrievalMode(mode)
        return RetrievalResponse(
            requested_mode=selected,
            path_used=selected,
            reason=f"call-{len(self.calls)}",
            results=[],
            final_top_k=top_k,
        )


def _cached(
    delegate: StubCoordinator,
    clock,
    *,
    corpus_version: str = "corpus-v1",
    settings_version: str = "settings-v1",
) -> CachedRetrievalCoordinator:
    return CachedRetrievalCoordinator(
        delegate,
        corpus_version,
        settings_version,
        RetrievalCache(ttl_seconds=10, max_entries=4, clock=clock),
    )


def test_cache_miss_then_normalized_query_hit() -> None:
    now = [100.0]
    delegate = StubCoordinator()
    cache = RetrievalCache(ttl_seconds=10, max_entries=4, clock=lambda: now[0])
    cached = CachedRetrievalCoordinator(
        delegate, "corpus-v1", "settings-v1", cache
    )

    first = cached.retrieve("  Partial   REFUNDS ", "hybrid", 5)
    second = cached.retrieve("partial refunds", RetrievalMode.HYBRID, 5)

    assert first.reason == "call-1"
    assert second.reason == "call-1"
    assert len(delegate.calls) == 1
    assert cache.misses == 1
    assert cache.hits == 1


def test_cache_entry_expires() -> None:
    now = [100.0]
    delegate = StubCoordinator()
    cached = _cached(delegate, lambda: now[0])

    cached.retrieve("partial refunds", "hybrid", 5)
    now[0] = 111.0
    refreshed = cached.retrieve("partial refunds", "hybrid", 5)

    assert refreshed.reason == "call-2"
    assert len(delegate.calls) == 2


def test_cache_separates_top_k_corpus_and_settings_versions() -> None:
    now = [100.0]
    delegate = StubCoordinator()
    cache = RetrievalCache(ttl_seconds=10, max_entries=8, clock=lambda: now[0])
    first = CachedRetrievalCoordinator(
        delegate, "corpus-v1", "settings-v1", cache
    )
    second_corpus = CachedRetrievalCoordinator(
        delegate, "corpus-v2", "settings-v1", cache
    )
    second_settings = CachedRetrievalCoordinator(
        delegate, "corpus-v1", "settings-v2", cache
    )

    first.retrieve("partial refunds", "hybrid", 5)
    first.retrieve("partial refunds", "hybrid", 3)
    second_corpus.retrieve("partial refunds", "hybrid", 5)
    second_settings.retrieve("partial refunds", "hybrid", 5)

    assert len(delegate.calls) == 4


def test_disabled_cache_always_delegates() -> None:
    delegate = StubCoordinator()
    cached = CachedRetrievalCoordinator(
        delegate,
        "corpus-v1",
        "settings-v1",
        cache=None,
    )

    cached.retrieve("partial refunds", "hybrid", 5)
    cached.retrieve("partial refunds", "hybrid", 5)

    assert len(delegate.calls) == 2


def test_cache_evicts_least_recently_used_entry_at_capacity() -> None:
    now = [100.0]
    delegate = StubCoordinator()
    cache = RetrievalCache(ttl_seconds=10, max_entries=2, clock=lambda: now[0])
    cached = CachedRetrievalCoordinator(
        delegate, "corpus-v1", "settings-v1", cache
    )

    cached.retrieve("query one", "hybrid", 5)
    cached.retrieve("query two", "hybrid", 5)
    cached.retrieve("query one", "hybrid", 5)  # make query two the LRU
    cached.retrieve("query three", "hybrid", 5)
    cached.retrieve("query two", "hybrid", 5)

    assert len(delegate.calls) == 4

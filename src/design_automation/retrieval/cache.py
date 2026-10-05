"""Bounded in-process TTL/LRU caching for final retrieval responses."""

from __future__ import annotations

import copy
import hashlib
import logging
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Protocol

from design_automation.models import RetrievalMode, RetrievalResponse
from design_automation.core.observability import log_event


logger = logging.getLogger(__name__)


class RetrievalDelegate(Protocol):
    def retrieve(
        self,
        query: str,
        mode: RetrievalMode | str,
        top_k: int,
    ) -> RetrievalResponse: ...


@dataclass(frozen=True)
class RetrievalCacheKey:
    normalized_query: str
    mode: str
    top_k: int
    corpus_version: str
    settings_version: str


@dataclass
class _CacheEntry:
    expires_at: float
    response: RetrievalResponse


class RetrievalCache:
    """A process-local TTL cache with least-recently-used eviction."""

    def __init__(
        self,
        ttl_seconds: float,
        max_entries: int,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[RetrievalCacheKey, _CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: RetrievalCacheKey) -> RetrievalResponse | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return None
            if entry.expires_at <= self._clock():
                del self._entries[key]
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            return copy.deepcopy(entry.response)

    def put(self, key: RetrievalCacheKey, response: RetrievalResponse) -> None:
        with self._lock:
            self._entries[key] = _CacheEntry(
                expires_at=self._clock() + self._ttl_seconds,
                response=copy.deepcopy(response),
            )
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


def normalize_cache_query(query: str) -> str:
    return " ".join(query.split()).casefold()


class CachedRetrievalCoordinator:
    """Cache a coordinator's final response without changing retrieval logic."""

    def __init__(
        self,
        delegate: RetrievalDelegate,
        corpus_version: str,
        settings_version: str,
        cache: RetrievalCache | None,
    ) -> None:
        self._delegate = delegate
        self.corpus_version = corpus_version
        self.settings_version = settings_version
        self._cache = cache

    def retrieve(
        self,
        query: str,
        mode: RetrievalMode | str,
        top_k: int,
    ) -> RetrievalResponse:
        selected_mode = RetrievalMode(mode)
        key = RetrievalCacheKey(
            normalized_query=normalize_cache_query(query),
            mode=selected_mode.value,
            top_k=top_k,
            corpus_version=self.corpus_version,
            settings_version=self.settings_version,
        )
        query_fingerprint = hashlib.sha256(
            key.normalized_query.encode("utf-8")
        ).hexdigest()[:12]

        if self._cache is not None:
            cached = self._cache.get(key)
            if cached is not None:
                log_event(
                    logger,
                    logging.INFO,
                    "retrieval_cache_hit",
                    mode=selected_mode.value,
                    top_k=top_k,
                    query_fingerprint=query_fingerprint,
                    corpus_version=self.corpus_version,
                )
                return cached

        log_event(
            logger,
            logging.INFO,
            "retrieval_cache_miss",
            mode=selected_mode.value,
            top_k=top_k,
            query_fingerprint=query_fingerprint,
            corpus_version=self.corpus_version,
            cache_enabled=self._cache is not None,
        )
        response = self._delegate.retrieve(query, selected_mode, top_k)
        if self._cache is not None:
            self._cache.put(key, response)
        return response

"""Run the existing retrieval coordinator over manually labeled cases."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from typing import Protocol

from design_automation.evaluation.metrics import (
    first_relevant_rank,
    hit_at_k,
    mean_reciprocal_rank,
    precision_at_k,
    recall_at_k,
    unique_ranking,
)
from design_automation.evaluation.models import (
    GoldDataset,
    HYBRID_RERANKED_MODE,
    MetricsAtK,
    ModeAggregate,
    ModeQueryResult,
    QueryEvaluation,
    RetrievalEvaluationReport,
)
from design_automation.models import RetrievalMode, RetrievalResponse


class EvaluationRetriever(Protocol):
    def retrieve(
        self,
        query: str,
        mode: RetrievalMode | str,
        top_k: int,
    ) -> RetrievalResponse: ...


def _rank_value(rank: int | None) -> float:
    return float(rank) if rank is not None else float("inf")


def _diagnostics(modes: dict[str, ModeQueryResult]) -> tuple[str, ...]:
    dense_rank = modes[RetrievalMode.DENSE.value].first_relevant_rank
    lexical_rank = modes[RetrievalMode.LEXICAL.value].first_relevant_rank
    hybrid_rank = modes[RetrievalMode.HYBRID.value].first_relevant_rank
    messages: list[str] = []

    for name, rank in (
        ("dense", dense_rank),
        ("lexical", lexical_rank),
        ("hybrid", hybrid_rank),
    ):
        if rank is None:
            messages.append(f"{name}_candidate_miss_at_10")
        elif rank > 5:
            messages.append(f"{name}_relevant_ranked_low")

    dense_value = _rank_value(dense_rank)
    lexical_value = _rank_value(lexical_rank)
    hybrid_value = _rank_value(hybrid_rank)
    if lexical_value < dense_value:
        messages.append("bm25_beats_dense")
    elif dense_value < lexical_value:
        messages.append("dense_beats_bm25")
    best_first_stage = min(dense_value, lexical_value)
    if hybrid_value < best_first_stage:
        messages.append("hybrid_improves_first_relevant_rank")
    elif hybrid_value > best_first_stage:
        messages.append("hybrid_hurts_first_relevant_rank")
    reranked = modes.get(HYBRID_RERANKED_MODE)
    if reranked is not None:
        reranked_value = _rank_value(reranked.first_relevant_rank)
        if reranked_value < hybrid_value:
            messages.append("reranker_improves_hybrid_rank")
        elif reranked_value > hybrid_value:
            messages.append("reranker_hurts_hybrid_rank")
    return tuple(messages)


def _average_present(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None


def evaluate_retrieval(
    retriever: EvaluationRetriever,
    dataset: GoldDataset,
    ks: tuple[int, ...] = (1, 3, 5, 10),
    modes: tuple[RetrievalMode | str, ...] = (
        RetrievalMode.DENSE,
        RetrievalMode.LEXICAL,
        RetrievalMode.HYBRID,
    ),
) -> RetrievalEvaluationReport:
    """Evaluate every case once per mode at the largest requested K."""

    if not ks or any(k <= 0 for k in ks):
        raise ValueError("ks must contain positive integers")
    if tuple(sorted(set(ks))) != ks:
        raise ValueError("ks must be unique and ascending")
    max_k = max(ks)
    query_rows: list[QueryEvaluation] = []

    mode_names = tuple(
        mode.value if isinstance(mode, RetrievalMode) else mode for mode in modes
    )
    if len(set(mode_names)) != len(mode_names):
        raise ValueError("modes must be unique")
    raw_by_mode: dict[str, list[ModeQueryResult]] = {
        mode_name: [] for mode_name in mode_names
    }
    for case in dataset.cases:
        case_modes: dict[str, ModeQueryResult] = {}
        for mode, mode_name in zip(modes, mode_names, strict=True):
            started = perf_counter()
            response = retriever.retrieve(case.query, mode, max_k)
            latency_ms = (perf_counter() - started) * 1000
            chunk_ids = tuple(
                unique_ranking([result.chunk_id for result in response.results])
            )
            mode_result = ModeQueryResult(
                mode=mode_name,
                retrieved_chunk_ids=chunk_ids,
                first_relevant_rank=first_relevant_rank(
                    chunk_ids,
                    case.relevant_chunk_ids,
                    max_k,
                ),
                latency_ms=latency_ms,
                candidate_retrieval_latency_ms=(
                    response.candidate_retrieval_latency_ms
                ),
                reranker_latency_ms=response.reranker_latency_ms,
            )
            case_modes[mode_name] = mode_result
            raw_by_mode[mode_name].append(mode_result)
        query_rows.append(
            QueryEvaluation(
                case_id=case.id,
                category=case.category,
                query=case.query,
                relevant_chunk_ids=case.relevant_chunk_ids,
                modes=case_modes,
                diagnostics=_diagnostics(case_modes),
            )
        )

    aggregates: dict[str, ModeAggregate] = {}
    for mode_name in mode_names:
        mode_rows = raw_by_mode[mode_name]
        metrics_at_k: dict[int, MetricsAtK] = {}
        for k in ks:
            metrics_at_k[k] = MetricsAtK(
                precision=sum(
                    precision_at_k(
                        row.retrieved_chunk_ids,
                        case.relevant_chunk_ids,
                        k,
                    )
                    for row, case in zip(mode_rows, dataset.cases, strict=True)
                )
                / len(dataset.cases),
                recall=sum(
                    recall_at_k(
                        row.retrieved_chunk_ids,
                        case.relevant_chunk_ids,
                        k,
                    )
                    for row, case in zip(mode_rows, dataset.cases, strict=True)
                )
                / len(dataset.cases),
                hit_rate=sum(
                    hit_at_k(
                        row.retrieved_chunk_ids,
                        case.relevant_chunk_ids,
                        k,
                    )
                    for row, case in zip(mode_rows, dataset.cases, strict=True)
                )
                / len(dataset.cases),
            )
        rankings = [
            (row.retrieved_chunk_ids, case.relevant_chunk_ids)
            for row, case in zip(mode_rows, dataset.cases, strict=True)
        ]
        aggregates[mode_name] = ModeAggregate(
            mode=mode_name,
            metrics_at_k=metrics_at_k,
            mrr=mean_reciprocal_rank(rankings, max_k),
            average_latency_ms=sum(row.latency_ms for row in mode_rows)
            / len(mode_rows),
            average_candidate_retrieval_latency_ms=_average_present(
                [row.candidate_retrieval_latency_ms for row in mode_rows]
            ),
            average_reranker_latency_ms=_average_present(
                [row.reranker_latency_ms for row in mode_rows]
            ),
        )

    return RetrievalEvaluationReport(
        dataset_version=dataset.dataset_version,
        corpus=dataset.corpus,
        case_count=len(dataset.cases),
        ks=ks,
        aggregates=aggregates,
        queries=tuple(query_rows),
    )


def report_as_dict(report: RetrievalEvaluationReport) -> dict[str, object]:
    """Convert dataclasses and integer K keys into readable JSON data."""

    payload = asdict(report)
    for aggregate in payload["aggregates"].values():
        aggregate["metrics_at_k"] = {
            str(k): value for k, value in aggregate["metrics_at_k"].items()
        }
    return payload


def write_report(path: Path, report: RetrievalEvaluationReport) -> None:
    """Write once so a later experiment cannot silently replace the baseline."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        json.dump(report_as_dict(report), output, indent=2, sort_keys=True)
        output.write("\n")

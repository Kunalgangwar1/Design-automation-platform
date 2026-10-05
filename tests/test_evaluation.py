"""Offline Day-10 retrieval and generation evaluation tests."""

import json
from pathlib import Path

import pytest

from design_automation.core import PROJECT_ROOT
from design_automation.evaluation.dataset import (
    EvaluationDatasetError,
    load_generation_case_set,
    load_gold_dataset,
)
from design_automation.evaluation.generation_metrics import (
    covered_expected_facts,
    evaluate_generation_captures,
)
from design_automation.evaluation.metrics import (
    hit_at_k,
    mean_reciprocal_rank,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from design_automation.evaluation.models import (
    HYBRID_RERANKED_MODE,
    CapturedGeneration,
)
from design_automation.evaluation.retrieval_runner import evaluate_retrieval
from design_automation.evaluation.reranker import (
    CrossEncoderReranker,
    RerankingEvaluationRetriever,
)
from design_automation.ingestion import ingest_documents
from design_automation.models import (
    RetrievalMode,
    RetrievalProvider,
    RetrievalResponse,
    RetrievalResult,
)


DATASET_PATH = PROJECT_ROOT / "data" / "evaluation" / "oms_rag_gold.json"
GENERATION_CASES_PATH = (
    PROJECT_ROOT / "data" / "evaluation" / "oms_generation_cases.json"
)


def _generation_cases():
    retrieval = load_gold_dataset(DATASET_PATH)
    return load_generation_case_set(
        GENERATION_CASES_PATH,
        {case.id: case for case in retrieval.cases},
    )


def test_precision_recall_hit_and_rank_with_multiple_relevant_chunks() -> None:
    retrieved = ["relevant-a", "noise", "relevant-b"]
    relevant = {"relevant-a", "relevant-b"}

    assert precision_at_k(retrieved, relevant, 1) == 1.0
    assert recall_at_k(retrieved, relevant, 1) == 0.5
    assert hit_at_k(retrieved, relevant, 1) == 1.0
    assert precision_at_k(retrieved, relevant, 3) == pytest.approx(2 / 3)
    assert recall_at_k(retrieved, relevant, 3) == 1.0
    assert reciprocal_rank(retrieved, relevant) == 1.0


def test_metrics_handle_no_relevant_result_and_result_outside_k() -> None:
    retrieved = ["noise-1", "noise-2", "relevant"]
    relevant = {"relevant"}

    assert precision_at_k(retrieved, relevant, 2) == 0.0
    assert recall_at_k(retrieved, relevant, 2) == 0.0
    assert hit_at_k(retrieved, relevant, 2) == 0.0
    assert reciprocal_rank(retrieved, relevant, max_k=2) == 0.0
    assert reciprocal_rank(retrieved, relevant) == pytest.approx(1 / 3)


def test_duplicate_retrieved_chunks_count_only_at_first_occurrence() -> None:
    retrieved = ["relevant", "relevant", "noise"]

    assert precision_at_k(retrieved, {"relevant"}, 2) == 0.5
    assert recall_at_k(retrieved, {"relevant"}, 2) == 1.0
    assert reciprocal_rank(retrieved, {"relevant"}) == 1.0


def test_mrr_averages_rank_one_rank_two_and_miss() -> None:
    rankings = [
        (["a"], {"a"}),
        (["noise", "b"], {"b"}),
        (["noise"], {"c"}),
    ]

    assert mean_reciprocal_rank(rankings) == pytest.approx((1 + 0.5 + 0) / 3)


def test_gold_dataset_uses_only_current_chunk_ids_and_expected_categories() -> None:
    chunks = ingest_documents().chunks
    dataset = load_gold_dataset(DATASET_PATH, {chunk.chunk_id for chunk in chunks})

    assert len(dataset.cases) == 20
    assert len({case.id for case in dataset.cases}) == 20
    assert sum(bool(case.expected_facts) for case in dataset.cases) == 11
    assert {
        "semantic_paraphrase",
        "exact_identifier",
        "api_interface",
        "business_rule",
        "ownership",
        "failure_handling",
        "architecture_relationship",
        "event_driven_behavior",
        "security_audit",
        "multi_evidence",
    }.issubset({case.category for case in dataset.cases})


def test_dataset_validation_rejects_unknown_chunk_id(tmp_path: Path) -> None:
    payload = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    payload["cases"][0]["relevant_chunk_ids"] = ["doc-invented-chunk-9999"]
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EvaluationDatasetError, match="unknown chunks"):
        load_gold_dataset(invalid_path, {"real-chunk"})


def _retrieval_result(chunk_id: str, rank: int) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        document_id="doc",
        section="Section",
        source="source.md",
        text="text",
        similarity_score=None,
        provider=RetrievalProvider.RRF,
        rank=rank,
        provider_score=0.02 / rank,
        dense_rank=rank,
        dense_score=0.8 / rank,
        lexical_rank=rank + 1,
        lexical_score=2.0 / rank,
        fusion_score=0.02 / rank,
        metadata={"owner": "test"},
    )


class StubEvaluationRetriever:
    def retrieve(
        self,
        query: str,
        mode: RetrievalMode | str,
        top_k: int,
    ) -> RetrievalResponse:
        selected = RetrievalMode(mode)
        rankings = {
            RetrievalMode.DENSE: ["noise", "relevant"],
            RetrievalMode.LEXICAL: ["relevant", "noise"],
            RetrievalMode.HYBRID: ["relevant", "noise"],
        }
        return RetrievalResponse(
            requested_mode=selected,
            path_used=selected,
            reason="stub",
            results=[
                _retrieval_result(chunk_id, rank)
                for rank, chunk_id in enumerate(rankings[selected], start=1)
            ],
            final_top_k=top_k,
        )


def test_retrieval_runner_calculates_mode_metrics_and_diagnostics() -> None:
    source = load_gold_dataset(DATASET_PATH)
    case = source.cases[0]
    one_case = type(source)(
        source.dataset_version,
        source.corpus,
        source.description,
        (type(case)(case.id, case.query, case.category, ("relevant",)),),
    )

    report = evaluate_retrieval(StubEvaluationRetriever(), one_case)

    assert report.aggregates["dense"].mrr == 0.5
    assert report.aggregates["lexical"].mrr == 1.0
    assert report.aggregates["hybrid"].metrics_at_k[1].hit_rate == 1.0
    assert "bm25_beats_dense" in report.queries[0].diagnostics


def test_reranker_reorders_only_the_hybrid_candidate_set() -> None:
    class ScoreSecondFirst:
        def score(self, query: str, texts: list[str]) -> list[float]:
            assert query == "query"
            return [0.1, 0.9]

    delegate = StubEvaluationRetriever()
    reranker = CrossEncoderReranker(ScoreSecondFirst())
    reranker = RerankingEvaluationRetriever(
        delegate,
        reranker,
        candidate_count=2,
    )

    response = reranker.retrieve("query", HYBRID_RERANKED_MODE, 2)

    assert [result.chunk_id for result in response.results] == ["noise", "relevant"]
    assert [result.reranker_score for result in response.results] == [0.9, 0.1]
    assert [result.provider_score for result in response.results] == [0.01, 0.02]
    assert response.candidate_retrieval_latency_ms is not None
    assert response.reranker_latency_ms is not None


def test_cross_encoder_reranker_preserves_candidate_diagnostics() -> None:
    class FixedScores:
        def score(self, query: str, texts: list[str]) -> list[float]:
            return [0.25, 0.75]

    candidates = [_retrieval_result("first", 1), _retrieval_result("second", 2)]
    original = candidates[1]

    outcome = CrossEncoderReranker(FixedScores()).rerank("query", candidates, 1)

    selected = outcome.results[0]
    assert selected.chunk_id == original.chunk_id
    assert selected.metadata == original.metadata
    assert selected.dense_score == original.dense_score
    assert selected.lexical_score == original.lexical_score
    assert selected.fusion_score == original.fusion_score
    assert selected.provider_score == original.provider_score
    assert selected.reranker_score == 0.75


def test_reranker_respects_candidate_depth_and_cannot_create_missing_result() -> None:
    class RecordingRetriever(StubEvaluationRetriever):
        requested_top_k: int | None = None

        def retrieve(self, query, mode, top_k):
            self.requested_top_k = top_k
            return super().retrieve(query, mode, top_k)

    class PreferEverything:
        def score(self, query: str, texts: list[str]) -> list[float]:
            return list(reversed(range(len(texts))))

    delegate = RecordingRetriever()
    wrapper = RerankingEvaluationRetriever(
        delegate,
        CrossEncoderReranker(PreferEverything()),
        candidate_count=10,
    )

    response = wrapper.retrieve("query", HYBRID_RERANKED_MODE, 5)

    assert delegate.requested_top_k == 10
    assert "absent" not in {result.chunk_id for result in response.results}
    unchanged = wrapper.retrieve("query", RetrievalMode.HYBRID, 2)
    assert [result.chunk_id for result in unchanged.results] == ["relevant", "noise"]


def test_reranked_mode_participates_in_offline_metrics() -> None:
    class ReverseScores:
        def score(self, query: str, texts: list[str]) -> list[float]:
            return [0.1, 0.9]

    source = load_gold_dataset(DATASET_PATH)
    case = source.cases[0]
    one_case = type(source)(
        source.dataset_version,
        source.corpus,
        source.description,
        (type(case)(case.id, case.query, case.category, ("relevant",)),),
    )
    wrapper = RerankingEvaluationRetriever(
        StubEvaluationRetriever(),
        CrossEncoderReranker(ReverseScores()),
        candidate_count=2,
    )

    report = evaluate_retrieval(
        wrapper,
        one_case,
        modes=(
            RetrievalMode.DENSE,
            RetrievalMode.LEXICAL,
            RetrievalMode.HYBRID,
            HYBRID_RERANKED_MODE,
        ),
    )

    assert report.aggregates[HYBRID_RERANKED_MODE].mrr == 0.5
    assert report.aggregates[HYBRID_RERANKED_MODE].average_reranker_latency_ms \
        is not None
    assert "reranker_hurts_hybrid_rank" in report.queries[0].diagnostics


def test_expected_fact_coverage_and_provenance_are_separate() -> None:
    chunks = ingest_documents().chunks
    dataset = _generation_cases()
    case = next(item for item in dataset.cases if item.id == "support-partial-refund-api")
    text = (
        "## Impact Analysis\n\nThe supplied contract documents that the Support Portal "
        "calls Order Service through POST /orders/{order_id}/refunds. This is "
        "grounded in interfaces/refund_interfaces.md and "
        "doc-e14e6a30041c-chunk-0001. Further business behavior requires human "
        "review and must not be inferred beyond this supplied interface evidence."
    )
    capture = CapturedGeneration(
        case_id=case.id,
        requested_section="impact_analysis",
        provider="fake",
        model="offline",
        generated_text=text,
        latency_ms=10,
        input_tokens=100,
        output_tokens=50,
        supplied_chunk_ids=("doc-e14e6a30041c-chunk-0001",),
        supplied_source_paths=("interfaces/refund_interfaces.md",),
        answer_relevance="pass",
        unsupported_claims_reviewed=True,
    )

    report = evaluate_generation_captures(dataset, (capture,), chunks)
    row = report.cases[0]

    assert covered_expected_facts(case, text) == (
        "The Support Portal calls Order Service with POST /orders/{order_id}/refunds.",
    )
    assert row.fact_coverage == 1.0
    assert row.provenance_valid is True
    assert row.structural_valid is True
    assert row.validation_valid is True
    assert row.unsupported_claim_review_required is False
    assert report.aggregate.human_relevance_pass_rate == 1.0
    assert report.aggregate.prepared_case_count == 10
    assert report.aggregate.evaluated_case_count == 1
    assert len(report.missing_result_case_ids) == 9


def test_generation_evaluation_flags_invented_chunk_citation() -> None:
    chunks = ingest_documents().chunks
    dataset = _generation_cases()
    case = next(item for item in dataset.cases if item.id == "completed-order-eligibility")
    text = (
        "## Impact Analysis\n\nOnly a COMPLETED order is eligible for the requested "
        "item-level partial refund. This statement cites "
        "doc-f69d08d8af4b-chunk-9999, which was not supplied. Additional text "
        "keeps the captured answer long enough for the structural validation check."
    )
    capture = CapturedGeneration(
        case_id=case.id,
        requested_section="impact_analysis",
        provider="fake",
        model="offline",
        generated_text=text,
        latency_ms=10,
        input_tokens=None,
        output_tokens=None,
        supplied_chunk_ids=("doc-f69d08d8af4b-chunk-0001",),
        supplied_source_paths=("requirements/partial_refunds.md",),
    )

    report = evaluate_generation_captures(dataset, (capture,), chunks)

    assert report.cases[0].provenance_valid is False
    assert report.cases[0].structural_valid is True
    assert report.cases[0].validation_valid is False
    assert "doc-f69d08d8af4b-chunk-9999" in report.cases[0].validation_errors[0]


def test_generation_case_definitions_are_the_ten_prepared_cases() -> None:
    cases = _generation_cases()

    assert len(cases.cases) == 10
    assert {case.id for case in cases.cases} == {
        "support-partial-refund-api",
        "refund-ownership-boundary",
        "completed-order-eligibility",
        "shipping-first-release",
        "gateway-timeout-reconciliation",
        "event-order-items-refunded",
        "inventory-refund-inputs",
        "seven-year-audit-retention",
        "pii-card-data-constraints",
        "cancellation-versus-partial-refund",
    }
    assert all(case.expected_facts for case in cases.cases)


def test_expected_fact_coverage_reports_partial_and_zero_coverage() -> None:
    cases = _generation_cases()
    ownership = next(
        case for case in cases.cases if case.id == "refund-ownership-boundary"
    )

    partial = covered_expected_facts(
        ownership,
        "Order Service owns refund eligibility.",
    )
    zero = covered_expected_facts(ownership, "No ownership evidence was found.")

    assert partial == ("Order Service owns refund business eligibility.",)
    assert zero == ()


def test_generation_evaluation_allows_no_captures_and_reports_missing_results() -> None:
    cases = _generation_cases()
    report = evaluate_generation_captures(cases, (), ingest_documents().chunks)

    assert report.aggregate.prepared_case_count == 10
    assert report.aggregate.evaluated_case_count == 0
    assert report.aggregate.structural_validation_rate is None
    assert report.aggregate.provenance_validation_rate is None
    assert report.aggregate.average_fact_coverage is None
    assert report.aggregate.human_relevance_pass_rate is None
    assert len(report.missing_result_case_ids) == 10


def test_generation_aggregate_uses_only_available_reviewed_results() -> None:
    cases = _generation_cases()
    chunks = ingest_documents().chunks
    support = CapturedGeneration(
        case_id="support-partial-refund-api",
        requested_section="impact_analysis",
        provider="synthetic-test",
        model="offline-stub",
        generated_text=(
            "## Impact Analysis\n\nSupport Portal calls Order Service using "
            "POST /orders/{order_id}/refunds. This synthetic unit-test answer "
            "contains enough deterministic text for structural validation and "
            "does not represent a hosted model response."
        ),
        latency_ms=10,
        input_tokens=20,
        output_tokens=30,
        supplied_chunk_ids=("doc-e14e6a30041c-chunk-0001",),
        supplied_source_paths=("interfaces/refund_interfaces.md",),
        answer_relevance="pass",
        unsupported_claims_reviewed=True,
    )
    incomplete = CapturedGeneration(
        case_id="completed-order-eligibility",
        requested_section="impact_analysis",
        provider="synthetic-test",
        model="offline-stub",
        generated_text=(
            "## Impact Analysis\n\nThis synthetic unit-test answer deliberately "
            "omits the expected order-status fact so partial aggregate behavior "
            "can be tested without invoking any hosted model or external service."
        ),
        latency_ms=30,
        input_tokens=40,
        output_tokens=50,
        supplied_chunk_ids=("doc-f69d08d8af4b-chunk-0001",),
        supplied_source_paths=("requirements/partial_refunds.md",),
        answer_relevance="fail",
        unsupported_claims=("The answer omitted the required eligibility fact.",),
        unsupported_claims_reviewed=True,
    )

    report = evaluate_generation_captures(cases, (support, incomplete), chunks)

    assert report.aggregate.prepared_case_count == 10
    assert report.aggregate.evaluated_case_count == 2
    assert report.aggregate.average_fact_coverage == 0.5
    assert report.aggregate.human_relevance_reviewed == 2
    assert report.aggregate.human_relevance_pass_rate == 0.5
    assert len(report.missing_result_case_ids) == 8

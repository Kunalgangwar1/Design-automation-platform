"""Small models for repeatable offline evaluation reports."""

from dataclasses import dataclass, field


HYBRID_RERANKED_MODE = "hybrid_reranked"


@dataclass(frozen=True)
class ExpectedFact:
    """A human-written fact with deterministic coverage alternatives."""

    statement: str
    match_any: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class EvaluationCase:
    """One manually labeled query against the fixed OMS corpus."""

    id: str
    query: str
    category: str
    relevant_chunk_ids: tuple[str, ...]
    notes: str = ""
    expected_facts: tuple[ExpectedFact, ...] = ()


@dataclass(frozen=True)
class GoldDataset:
    """Versioned collection of manually curated evaluation cases."""

    dataset_version: int
    corpus: str
    description: str
    cases: tuple[EvaluationCase, ...]


@dataclass(frozen=True)
class GenerationEvaluationCase:
    """One manually prepared question and its evidence-backed expected facts."""

    id: str
    query: str
    category: str
    expected_facts: tuple[ExpectedFact, ...]
    notes: str = ""


@dataclass(frozen=True)
class GenerationCaseSet:
    """Generation definitions kept separate from retrieval relevance labels."""

    dataset_version: int
    corpus: str
    description: str
    cases: tuple[GenerationEvaluationCase, ...]


@dataclass(frozen=True)
class MetricsAtK:
    precision: float
    recall: float
    hit_rate: float


@dataclass(frozen=True)
class ModeAggregate:
    mode: str
    metrics_at_k: dict[int, MetricsAtK]
    mrr: float
    average_latency_ms: float
    average_candidate_retrieval_latency_ms: float | None = None
    average_reranker_latency_ms: float | None = None


@dataclass(frozen=True)
class ModeQueryResult:
    mode: str
    retrieved_chunk_ids: tuple[str, ...]
    first_relevant_rank: int | None
    latency_ms: float
    candidate_retrieval_latency_ms: float | None = None
    reranker_latency_ms: float | None = None


@dataclass(frozen=True)
class QueryEvaluation:
    case_id: str
    category: str
    query: str
    relevant_chunk_ids: tuple[str, ...]
    modes: dict[str, ModeQueryResult]
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class RetrievalEvaluationReport:
    dataset_version: int
    corpus: str
    case_count: int
    ks: tuple[int, ...]
    aggregates: dict[str, ModeAggregate]
    queries: tuple[QueryEvaluation, ...]
    dataset_sha256: str = ""
    embedding_model: str = ""
    corpus_chunk_ids: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CapturedGeneration:
    """Provider output plus the exact evidence supplied for that call."""

    case_id: str
    requested_section: str
    provider: str
    model: str
    generated_text: str
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    supplied_chunk_ids: tuple[str, ...]
    supplied_source_paths: tuple[str, ...]
    answer_relevance: str = "not_reviewed"
    unsupported_claims: tuple[str, ...] = ()
    unsupported_claims_reviewed: bool = False


@dataclass(frozen=True)
class GenerationCaseEvaluation:
    case_id: str
    query: str
    expected_facts: tuple[str, ...]
    covered_facts: tuple[str, ...]
    uncovered_facts: tuple[str, ...]
    fact_coverage: float
    provider: str
    model: str
    structural_valid: bool
    validation_valid: bool
    validation_errors: tuple[str, ...]
    validation_warnings: tuple[str, ...]
    provenance_valid: bool
    answer_relevance: str
    unsupported_claims: tuple[str, ...]
    unsupported_claim_review_required: bool
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None


@dataclass(frozen=True)
class GenerationAggregate:
    prepared_case_count: int
    evaluated_case_count: int
    structural_validation_rate: float | None
    provenance_validation_rate: float | None
    average_fact_coverage: float | None
    average_latency_ms: float | None
    average_input_tokens: float | None
    average_output_tokens: float | None
    human_relevance_reviewed: int
    human_relevance_pass_rate: float | None

    @property
    def case_count(self) -> int:
        """Backward-compatible name for the number of evaluated captures."""

        return self.evaluated_case_count


@dataclass(frozen=True)
class GenerationEvaluationReport:
    dataset_version: int
    corpus: str
    aggregate: GenerationAggregate
    cases: tuple[GenerationCaseEvaluation, ...]
    missing_result_case_ids: tuple[str, ...] = ()

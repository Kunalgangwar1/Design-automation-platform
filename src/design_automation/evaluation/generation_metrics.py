"""Human-first, deterministic diagnostics for captured generation outputs."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from design_automation.evaluation.dataset import EvaluationDatasetError
from design_automation.evaluation.models import (
    CapturedGeneration,
    GenerationAggregate,
    GenerationCaseSet,
    GenerationCaseEvaluation,
    GenerationEvaluationCase,
    GenerationEvaluationReport,
)
from design_automation.generation.validation import validate_generation
from design_automation.models import DocumentChunk, DocumentEvidence, GroundedContext


RELEVANCE_LABELS = {"pass", "fail", "not_reviewed"}


def _required_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvaluationDatasetError(f"{name} must be a non-empty string")
    return value.strip()


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise EvaluationDatasetError(f"{name} must be a string")
    return value


def _optional_int(value: Any, name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or value < 0:
        raise EvaluationDatasetError(f"{name} must be a non-negative integer or null")
    return value


def _boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise EvaluationDatasetError(f"{name} must be a boolean")
    return value


def _string_tuple(value: Any, name: str, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise EvaluationDatasetError(f"{name} must be a non-empty list")
    result = tuple(_required_string(item, name) for item in value)
    if len(set(result)) != len(result):
        raise EvaluationDatasetError(f"{name} must not contain duplicates")
    return result


def load_generation_captures(path: Path) -> tuple[CapturedGeneration, ...]:
    """Load previously captured outputs; this function never calls a provider."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationDatasetError(f"Could not load generation captures: {path}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise EvaluationDatasetError("Generation capture root must contain a results list")
    captures: list[CapturedGeneration] = []
    seen: set[str] = set()
    for raw in payload["results"]:
        if not isinstance(raw, dict):
            raise EvaluationDatasetError("Each generation capture must be an object")
        case_id = _required_string(raw.get("case_id"), "case_id")
        if case_id in seen:
            raise EvaluationDatasetError(f"Duplicate generation capture: {case_id}")
        seen.add(case_id)
        relevance = str(raw.get("answer_relevance", "not_reviewed"))
        if relevance not in RELEVANCE_LABELS:
            raise EvaluationDatasetError(
                f"answer_relevance must be one of {sorted(RELEVANCE_LABELS)}"
            )
        latency = raw.get("latency_ms")
        if not isinstance(latency, int) or latency < 0:
            raise EvaluationDatasetError("latency_ms must be a non-negative integer")
        captures.append(
            CapturedGeneration(
                case_id=case_id,
                requested_section=_required_string(
                    raw.get("requested_section"), "requested_section"
                ),
                provider=_required_string(raw.get("provider"), "provider"),
                model=_required_string(raw.get("model"), "model"),
                generated_text=_string(
                    raw.get("generated_text"), "generated_text"
                ),
                latency_ms=latency,
                input_tokens=_optional_int(raw.get("input_tokens"), "input_tokens"),
                output_tokens=_optional_int(raw.get("output_tokens"), "output_tokens"),
                supplied_chunk_ids=_string_tuple(
                    raw.get("supplied_chunk_ids"), "supplied_chunk_ids"
                ),
                supplied_source_paths=_string_tuple(
                    raw.get("supplied_source_paths"), "supplied_source_paths"
                ),
                answer_relevance=relevance,
                unsupported_claims=_string_tuple(
                    raw.get("unsupported_claims", []),
                    "unsupported_claims",
                    allow_empty=True,
                ),
                unsupported_claims_reviewed=_boolean(
                    raw.get("unsupported_claims_reviewed", False),
                    "unsupported_claims_reviewed",
                ),
            )
        )
    return tuple(captures)


def _normalized(text: str) -> str:
    return " ".join(text.casefold().split())


def covered_expected_facts(
    case: GenerationEvaluationCase,
    text: str,
) -> tuple[str, ...]:
    """Apply curated literal term groups as a transparent coverage proxy."""

    normalized_text = _normalized(text)
    covered: list[str] = []
    for fact in case.expected_facts:
        if any(
            all(_normalized(term) in normalized_text for term in alternative)
            for alternative in fact.match_any
        ):
            covered.append(fact.statement)
    return tuple(covered)


def _average_optional(values: list[int | None]) -> float | None:
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None


def evaluate_generation_captures(
    dataset: GenerationCaseSet,
    captures: tuple[CapturedGeneration, ...],
    chunks: list[DocumentChunk],
) -> GenerationEvaluationReport:
    """Evaluate captured output without invoking retrieval, graph, or an LLM."""

    cases_by_id = {case.id: case for case in dataset.cases}
    chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    known_sources = {
        str(chunk.metadata.get("source_path", "")) for chunk in chunks
    }
    rows: list[GenerationCaseEvaluation] = []

    for capture in captures:
        case = cases_by_id.get(capture.case_id)
        if case is None:
            raise EvaluationDatasetError(
                f"Generation capture references unknown case: {capture.case_id}"
            )
        if not case.expected_facts:
            raise EvaluationDatasetError(
                f"Generation case has no manually labeled expected facts: {case.id}"
            )
        unknown_chunks = sorted(set(capture.supplied_chunk_ids) - chunks_by_id.keys())
        if unknown_chunks:
            raise EvaluationDatasetError(
                f"Capture {case.id} supplies unknown chunks: {', '.join(unknown_chunks)}"
            )
        unknown_sources = sorted(
            set(capture.supplied_source_paths) - known_sources
        )
        if unknown_sources:
            raise EvaluationDatasetError(
                f"Capture {case.id} supplies unknown sources: {', '.join(unknown_sources)}"
            )

        evidence: list[DocumentEvidence] = []
        for rank, chunk_id in enumerate(capture.supplied_chunk_ids, start=1):
            chunk = chunks_by_id[chunk_id]
            evidence.append(
                DocumentEvidence(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    document_title=str(chunk.metadata.get("title", chunk.document_id)),
                    source_path=str(chunk.metadata.get("source_path", "")),
                    section=chunk.section,
                    text=chunk.text,
                    retrieval_rank=rank,
                    metadata=dict(chunk.metadata),
                )
            )
        context = GroundedContext(
            requirement=case.query,
            requested_section=capture.requested_section,
            document_evidence=evidence,
            source_provenance=list(capture.supplied_source_paths),
        )
        validation = validate_generation(
            capture.generated_text,
            capture.requested_section,
            context,
        )
        provenance_errors = [
            error
            for error in validation.errors
            if "absent from GroundedContext" in error
        ]
        structural_errors = [
            error for error in validation.errors if error not in provenance_errors
        ]
        covered = covered_expected_facts(case, capture.generated_text)
        expected = tuple(fact.statement for fact in case.expected_facts)
        covered_set = set(covered)
        rows.append(
            GenerationCaseEvaluation(
                case_id=case.id,
                query=case.query,
                expected_facts=expected,
                covered_facts=covered,
                uncovered_facts=tuple(
                    fact for fact in expected if fact not in covered_set
                ),
                fact_coverage=len(covered) / len(expected),
                provider=capture.provider,
                model=capture.model,
                structural_valid=not structural_errors,
                validation_valid=validation.valid,
                validation_errors=tuple(validation.errors),
                validation_warnings=tuple(validation.warnings),
                provenance_valid=not provenance_errors,
                answer_relevance=capture.answer_relevance,
                unsupported_claims=capture.unsupported_claims,
                unsupported_claim_review_required=(
                    not capture.unsupported_claims_reviewed
                ),
                latency_ms=capture.latency_ms,
                input_tokens=capture.input_tokens,
                output_tokens=capture.output_tokens,
            )
        )

    reviewed = [row for row in rows if row.answer_relevance != "not_reviewed"]
    evaluated_count = len(rows)
    aggregate = GenerationAggregate(
        prepared_case_count=len(dataset.cases),
        evaluated_case_count=evaluated_count,
        structural_validation_rate=(
            sum(row.structural_valid for row in rows) / evaluated_count
            if rows
            else None
        ),
        provenance_validation_rate=(
            sum(row.provenance_valid for row in rows) / evaluated_count
            if rows
            else None
        ),
        average_fact_coverage=(
            sum(row.fact_coverage for row in rows) / evaluated_count
            if rows
            else None
        ),
        average_latency_ms=(
            sum(row.latency_ms for row in rows) / evaluated_count
            if rows
            else None
        ),
        average_input_tokens=_average_optional([row.input_tokens for row in rows]),
        average_output_tokens=_average_optional([row.output_tokens for row in rows]),
        human_relevance_reviewed=len(reviewed),
        human_relevance_pass_rate=(
            sum(row.answer_relevance == "pass" for row in reviewed) / len(reviewed)
            if reviewed
            else None
        ),
    )
    return GenerationEvaluationReport(
        dataset_version=dataset.dataset_version,
        corpus=dataset.corpus,
        aggregate=aggregate,
        cases=tuple(rows),
        missing_result_case_ids=tuple(
            case.id
            for case in dataset.cases
            if case.id not in {capture.case_id for capture in captures}
        ),
    )


def write_generation_report(path: Path, report: GenerationEvaluationReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        json.dump(asdict(report), output, indent=2, sort_keys=True)
        output.write("\n")

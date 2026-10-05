"""Load and validate the manually curated OMS evaluation dataset."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from design_automation.evaluation.models import (
    EvaluationCase,
    ExpectedFact,
    GenerationCaseSet,
    GenerationEvaluationCase,
    GoldDataset,
)


class EvaluationDatasetError(ValueError):
    """Raised when evaluation ground truth is malformed or stale."""


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvaluationDatasetError(f"{field_name} must be a non-empty string")
    return value.strip()


def _expected_fact(raw: Any, case_id: str) -> ExpectedFact:
    if not isinstance(raw, dict):
        raise EvaluationDatasetError(
            f"Case {case_id} expected_facts entries must be objects"
        )
    statement = _required_string(raw.get("statement"), "expected fact statement")
    raw_alternatives = raw.get("match_any")
    if not isinstance(raw_alternatives, list) or not raw_alternatives:
        raise EvaluationDatasetError(
            f"Case {case_id} expected fact must define non-empty match_any"
        )
    alternatives: list[tuple[str, ...]] = []
    for raw_terms in raw_alternatives:
        if not isinstance(raw_terms, list) or not raw_terms:
            raise EvaluationDatasetError(
                f"Case {case_id} match_any alternatives must be non-empty lists"
            )
        terms = tuple(
            _required_string(term, "expected fact match term")
            for term in raw_terms
        )
        alternatives.append(terms)
    return ExpectedFact(statement, tuple(alternatives))


def load_gold_dataset(
    path: Path,
    known_chunk_ids: set[str] | None = None,
) -> GoldDataset:
    """Load JSON labels and optionally reject IDs absent from the live corpus."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationDatasetError(f"Could not load evaluation dataset: {path}") from exc
    if not isinstance(payload, dict):
        raise EvaluationDatasetError("Evaluation dataset root must be an object")

    version = payload.get("dataset_version")
    if not isinstance(version, int) or version <= 0:
        raise EvaluationDatasetError("dataset_version must be a positive integer")
    corpus = _required_string(payload.get("corpus"), "corpus")
    description = _required_string(payload.get("description"), "description")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise EvaluationDatasetError("cases must be a non-empty list")

    cases: list[EvaluationCase] = []
    seen_case_ids: set[str] = set()
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            raise EvaluationDatasetError("Each evaluation case must be an object")
        case_id = _required_string(raw_case.get("id"), "case id")
        if case_id in seen_case_ids:
            raise EvaluationDatasetError(f"Duplicate evaluation case ID: {case_id}")
        seen_case_ids.add(case_id)

        raw_relevant = raw_case.get("relevant_chunk_ids")
        if not isinstance(raw_relevant, list) or not raw_relevant:
            raise EvaluationDatasetError(
                f"Case {case_id} must have at least one relevant chunk ID"
            )
        relevant = tuple(
            _required_string(chunk_id, "relevant chunk ID")
            for chunk_id in raw_relevant
        )
        if len(set(relevant)) != len(relevant):
            raise EvaluationDatasetError(
                f"Case {case_id} contains duplicate relevant chunk IDs"
            )
        if known_chunk_ids is not None:
            unknown = sorted(set(relevant) - known_chunk_ids)
            if unknown:
                raise EvaluationDatasetError(
                    f"Case {case_id} references unknown chunks: {', '.join(unknown)}"
                )

        raw_facts = raw_case.get("expected_facts", [])
        if not isinstance(raw_facts, list):
            raise EvaluationDatasetError(
                f"Case {case_id} expected_facts must be a list"
            )
        cases.append(
            EvaluationCase(
                id=case_id,
                query=_required_string(raw_case.get("query"), "query"),
                category=_required_string(raw_case.get("category"), "category"),
                relevant_chunk_ids=relevant,
                notes=str(raw_case.get("notes", "")).strip(),
                expected_facts=tuple(
                    _expected_fact(raw_fact, case_id) for raw_fact in raw_facts
                ),
            )
        )

    return GoldDataset(version, corpus, description, tuple(cases))


def load_generation_case_set(
    path: Path,
    retrieval_cases: dict[str, EvaluationCase] | None = None,
) -> GenerationCaseSet:
    """Load prepared generation cases without altering retrieval gold labels."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationDatasetError(
            f"Could not load generation case definitions: {path}"
        ) from exc
    if not isinstance(payload, dict):
        raise EvaluationDatasetError("Generation case root must be an object")

    version = payload.get("dataset_version")
    if not isinstance(version, int) or version <= 0:
        raise EvaluationDatasetError("dataset_version must be a positive integer")
    corpus = _required_string(payload.get("corpus"), "corpus")
    description = _required_string(payload.get("description"), "description")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise EvaluationDatasetError("generation cases must be a non-empty list")

    cases: list[GenerationEvaluationCase] = []
    seen: set[str] = set()
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            raise EvaluationDatasetError("Each generation case must be an object")
        case_id = _required_string(raw_case.get("id"), "generation case id")
        if case_id in seen:
            raise EvaluationDatasetError(f"Duplicate generation case ID: {case_id}")
        seen.add(case_id)
        query = _required_string(raw_case.get("query"), "generation query")
        if retrieval_cases is not None:
            retrieval_case = retrieval_cases.get(case_id)
            if retrieval_case is None:
                raise EvaluationDatasetError(
                    f"Generation case is absent from retrieval gold data: {case_id}"
                )
            if retrieval_case.query != query:
                raise EvaluationDatasetError(
                    f"Generation query differs from retrieval gold data: {case_id}"
                )
        raw_facts = raw_case.get("expected_facts")
        if not isinstance(raw_facts, list) or not raw_facts:
            raise EvaluationDatasetError(
                f"Generation case {case_id} requires expected_facts"
            )
        cases.append(
            GenerationEvaluationCase(
                id=case_id,
                query=query,
                category=_required_string(raw_case.get("category"), "category"),
                expected_facts=tuple(
                    _expected_fact(raw_fact, case_id) for raw_fact in raw_facts
                ),
                notes=str(raw_case.get("notes", "")).strip(),
            )
        )

    return GenerationCaseSet(version, corpus, description, tuple(cases))

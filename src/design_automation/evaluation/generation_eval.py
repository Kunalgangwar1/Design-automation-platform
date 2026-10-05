"""CLI for evaluating previously captured, grounded generation outputs."""

from __future__ import annotations

import argparse
from pathlib import Path

from design_automation.core import PROJECT_ROOT
from design_automation.evaluation.dataset import (
    load_generation_case_set,
    load_gold_dataset,
)
from design_automation.evaluation.generation_metrics import (
    evaluate_generation_captures,
    load_generation_captures,
    write_generation_report,
)
from design_automation.ingestion import ingest_documents


DEFAULT_DATASET_PATH = PROJECT_ROOT / "data" / "evaluation" / "oms_rag_gold.json"
DEFAULT_CASES_PATH = (
    PROJECT_ROOT / "data" / "evaluation" / "oms_generation_cases.json"
)
DEFAULT_RESULTS_PATH = (
    PROJECT_ROOT / "artifacts" / "evaluation" / "generation_results.json"
)
DEFAULT_REPORT_PATH = (
    PROJECT_ROOT / "artifacts" / "evaluation" / "generation_report.json"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate captured generation output; never call OpenAI or Gemini."
        )
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--no-save", action="store_true")
    return parser


def main() -> None:
    args = _parser().parse_args()
    if not args.results.exists():
        raise FileNotFoundError(
            f"No captured generation results at {args.results}. "
            "Capture provider/model/text/usage plus the exact supplied chunk IDs "
            "and source paths, then pass that JSON with --results."
        )
    if not args.no_save and args.output.exists():
        raise FileExistsError(
            f"Generation report already exists: {args.output}. "
            "Use --no-save or choose another --output path."
        )

    chunks = ingest_documents().chunks
    retrieval_dataset = load_gold_dataset(
        args.dataset,
        {chunk.chunk_id for chunk in chunks},
    )
    dataset = load_generation_case_set(
        args.cases,
        {case.id: case for case in retrieval_dataset.cases},
    )
    if dataset.corpus != retrieval_dataset.corpus:
        raise ValueError("Generation and retrieval datasets name different corpora")
    captures = load_generation_captures(args.results)
    report = evaluate_generation_captures(dataset, captures, chunks)

    aggregate = report.aggregate
    print(f"Dataset: {report.corpus} v{report.dataset_version}")
    print(f"Prepared generation cases: {aggregate.prepared_case_count}")
    print(f"Evaluated result cases: {aggregate.evaluated_case_count}")
    print(
        "Missing result cases: "
        + (", ".join(report.missing_result_case_ids) or "none")
    )
    print(
        "Structural validation rate: "
        + _format_optional(aggregate.structural_validation_rate)
    )
    print(
        "Provenance validation rate: "
        + _format_optional(aggregate.provenance_validation_rate)
    )
    print(
        "Average expected-fact coverage: "
        + _format_optional(aggregate.average_fact_coverage)
    )
    print(
        "Average provider latency: "
        + (
            f"{aggregate.average_latency_ms:.2f} ms"
            if aggregate.average_latency_ms is not None
            else "not available"
        )
    )
    print(f"Average input tokens: {aggregate.average_input_tokens}")
    print(f"Average output tokens: {aggregate.average_output_tokens}")
    print(f"Human relevance cases reviewed: {aggregate.human_relevance_reviewed}")
    print(f"Human relevance pass rate: {aggregate.human_relevance_pass_rate}")

    for row in report.cases:
        print(f"\n{row.case_id}: {row.query}")
        print(f"  provider/model: {row.provider}/{row.model}")
        print(f"  structural valid: {row.structural_valid}")
        print(f"  overall validation valid: {row.validation_valid}")
        print(f"  provenance valid: {row.provenance_valid}")
        print(f"  expected-fact coverage: {row.fact_coverage:.3f}")
        print(f"  expected facts: {'; '.join(row.expected_facts)}")
        print(f"  covered facts: {'; '.join(row.covered_facts) or '<none>'}")
        print(f"  not covered: {'; '.join(row.uncovered_facts) or '<none>'}")
        print(f"  answer relevance: {row.answer_relevance}")
        print(
            "  unsupported claims: "
            + (
                "; ".join(row.unsupported_claims)
                or (
                    "human review required"
                    if row.unsupported_claim_review_required
                    else "none identified by reviewer"
                )
            )
        )
        print(f"  latency/tokens: {row.latency_ms} ms, {row.input_tokens}/{row.output_tokens}")

    if not args.no_save:
        write_generation_report(args.output, report)
        print(f"\nGeneration evaluation written once to: {args.output}")


def _format_optional(value: float | None) -> str:
    return f"{value:.3f}" if value is not None else "not available"


if __name__ == "__main__":
    main()

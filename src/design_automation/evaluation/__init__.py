"""Offline retrieval and grounded-generation evaluation."""

from design_automation.evaluation.dataset import (
    load_generation_case_set,
    load_gold_dataset,
)
from design_automation.evaluation.metrics import (
    hit_at_k,
    mean_reciprocal_rank,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from design_automation.evaluation.models import (
    EvaluationCase,
    ExpectedFact,
    GenerationCaseSet,
    GenerationEvaluationCase,
    GoldDataset,
)

__all__ = [
    "EvaluationCase",
    "ExpectedFact",
    "GenerationCaseSet",
    "GenerationEvaluationCase",
    "GoldDataset",
    "hit_at_k",
    "load_gold_dataset",
    "load_generation_case_set",
    "mean_reciprocal_rank",
    "precision_at_k",
    "recall_at_k",
    "reciprocal_rank",
]

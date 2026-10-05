"""Deterministically render grounded context without invoking an LLM."""

from design_automation.models import (
    DocumentEvidence,
    GraphEvidence,
    GroundedContext,
    PromptTemplate,
    RenderedPrompt,
)


def _format_document(evidence: DocumentEvidence) -> str:
    return "\n".join(
        (
            f"[{evidence.retrieval_rank}] {evidence.document_title}",
            f"Source: {evidence.source_path}",
            f"Section: {evidence.section or '<none>'}",
            f"Chunk ID: {evidence.chunk_id}",
            "Text:",
            evidence.text,
        )
    )


def _format_graph(evidence: GraphEvidence, position: int) -> str:
    return (
        f"[{position}] ({evidence.source_entity}:{evidence.source_type})"
        f"-[:{evidence.relationship}]->"
        f"({evidence.target_entity}:{evidence.target_type})"
    )


class PromptBuilder:
    """Render a versioned prompt template with separated evidence blocks."""

    def build(
        self,
        template: PromptTemplate,
        context: GroundedContext,
    ) -> RenderedPrompt:
        documents = "\n\n".join(
            _format_document(evidence) for evidence in context.document_evidence
        ) or "<no document evidence>"
        graph = "\n".join(
            _format_graph(evidence, position)
            for position, evidence in enumerate(context.graph_evidence, start=1)
        ) or "<no graph evidence>"
        task_prompt = template.task_template.format(
            requirement=context.requirement,
            requested_section=context.requested_section,
            document_evidence=documents,
            graph_evidence=graph,
        )
        return RenderedPrompt(
            system_prompt=template.system_prompt,
            task_prompt=task_prompt,
            prompt_name=template.name,
            prompt_version=template.version,
            provenance_summary=list(context.source_provenance),
        )

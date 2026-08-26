"""Tests for the Bedrock adapter; no test makes an AWS network call."""

from dataclasses import dataclass, field
from typing import Any

import pytest
from botocore.exceptions import ClientError, NoCredentialsError

from design_automation.models import RetrievalOrigin, RetrievalProvider
from design_automation.retrieval.bedrock import (
    BedrockConfigurationError,
    BedrockKnowledgeBaseRetriever,
    BedrockRetrievalError,
)


@dataclass
class StubBedrockClient:
    response: dict[str, Any] = field(default_factory=dict)
    error: Exception | None = None
    request: dict[str, Any] | None = None

    def retrieve(self, **kwargs: Any) -> dict[str, Any]:
        self.request = kwargs
        if self.error:
            raise self.error
        return self.response


def _response() -> dict[str, Any]:
    return {
        "retrievalResults": [
            {
                "content": {"type": "TEXT", "text": "Retry the refund safely."},
                "documentId": "refund-runbook",
                "location": {
                    "type": "S3",
                    "s3Location": {"uri": "s3://enterprise/refund-runbook.md"},
                },
                "metadata": {"section": "Failure handling", "review_year": 2026},
                "score": 0.81,
            }
        ]
    }


def test_retrieve_request_uses_count_and_optional_metadata_filter() -> None:
    client = StubBedrockClient(_response())
    retriever = BedrockKnowledgeBaseRetriever(
        knowledge_base_id="KBTEST123",
        client=client,
    )
    metadata_filter = {"equals": {"key": "document_type", "value": "runbook"}}

    retriever.retrieve("refund timeout", top_k=7, metadata_filter=metadata_filter)

    assert client.request == {
        "knowledgeBaseId": "KBTEST123",
        "retrievalQuery": {"text": "refund timeout"},
        "retrievalConfiguration": {
            "vectorSearchConfiguration": {
                "numberOfResults": 7,
                "filter": metadata_filter,
            }
        },
    }


def test_response_is_converted_to_common_result_without_score_mixing() -> None:
    retriever = BedrockKnowledgeBaseRetriever(
        knowledge_base_id="KBTEST123",
        client=StubBedrockClient(_response()),
    )

    result = retriever.retrieve("refund timeout", top_k=1)[0]

    assert result.provider is RetrievalProvider.BEDROCK_KB
    assert result.origin is RetrievalOrigin.BEDROCK
    assert result.rank == 1
    assert result.provider_score == pytest.approx(0.81)
    assert result.similarity_score is None
    assert result.document_id == "refund-runbook"
    assert result.chunk_id == "bedrock-refund-runbook-0001"
    assert result.section == "Failure handling"
    assert result.source == "s3://enterprise/refund-runbook.md"
    assert result.text == "Retry the refund safely."
    assert result.metadata["review_year"] == 2026
    assert result.metadata["retrieval_provider"] == "bedrock_kb"


def test_missing_knowledge_base_configuration_has_actionable_error() -> None:
    with pytest.raises(BedrockConfigurationError, match="BEDROCK_KNOWLEDGE_BASE_ID"):
        BedrockKnowledgeBaseRetriever(knowledge_base_id=None, client=StubBedrockClient())


def test_missing_credentials_are_reported_without_an_aws_call() -> None:
    retriever = BedrockKnowledgeBaseRetriever(
        knowledge_base_id="KBTEST123",
        client=StubBedrockClient(error=NoCredentialsError()),
    )

    with pytest.raises(BedrockConfigurationError, match="credential provider chain"):
        retriever.retrieve("refund timeout")


def test_bedrock_client_failure_is_wrapped_with_service_details() -> None:
    error = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "not authorized"}},
        "Retrieve",
    )
    retriever = BedrockKnowledgeBaseRetriever(
        knowledge_base_id="KBTEST123",
        client=StubBedrockClient(error=error),
    )

    with pytest.raises(BedrockRetrievalError, match="AccessDeniedException"):
        retriever.retrieve("refund timeout")

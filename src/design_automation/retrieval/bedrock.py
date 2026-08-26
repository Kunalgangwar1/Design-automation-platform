"""Amazon Bedrock Knowledge Bases retrieval adapter."""

from __future__ import annotations

import json
from typing import Any, Protocol

from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    NoCredentialsError,
    PartialCredentialsError,
)

from design_automation.core.config import (
    AWS_REGION,
    BEDROCK_KNOWLEDGE_BASE_ID,
    BEDROCK_RESULT_COUNT,
)
from design_automation.models import RetrievalOrigin, RetrievalProvider, RetrievalResult


class BedrockConfigurationError(RuntimeError):
    """Raised when required Bedrock configuration is unavailable."""


class BedrockRetrievalError(RuntimeError):
    """Raised when the Bedrock Retrieve API call fails."""


class BedrockRuntimeClient(Protocol):
    """Minimal client contract used by the adapter and its tests."""

    def retrieve(self, **kwargs: Any) -> dict[str, Any]: ...


class BedrockKnowledgeBaseRetriever:
    """Call the Bedrock Knowledge Bases Retrieve API without generation."""

    def __init__(
        self,
        knowledge_base_id: str | None = BEDROCK_KNOWLEDGE_BASE_ID,
        region: str | None = AWS_REGION,
        default_result_count: int = BEDROCK_RESULT_COUNT,
        client: BedrockRuntimeClient | None = None,
    ) -> None:
        if not knowledge_base_id:
            raise BedrockConfigurationError(
                "BEDROCK_KNOWLEDGE_BASE_ID is not configured. Set it before "
                "using Bedrock retrieval."
            )
        if default_result_count <= 0:
            raise ValueError("default_result_count must be positive")
        if client is None and not region:
            raise BedrockConfigurationError(
                "AWS region is not configured. Set AWS_REGION or "
                "AWS_DEFAULT_REGION before using Bedrock retrieval."
            )

        if client is None:
            import boto3

            try:
                client = boto3.client("bedrock-agent-runtime", region_name=region)
            except BotoCoreError as exc:
                raise BedrockConfigurationError(
                    f"Unable to configure the Bedrock Agent Runtime client: {exc}"
                ) from exc

        self.knowledge_base_id = knowledge_base_id
        self.default_result_count = default_result_count
        self.client = client

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[RetrievalResult]:
        """Retrieve passages and adapt them to the common result model."""

        if not query.strip():
            raise ValueError("query cannot be empty")
        result_count = top_k or self.default_result_count
        if result_count <= 0:
            raise ValueError("top_k must be positive")

        vector_configuration: dict[str, Any] = {"numberOfResults": result_count}
        if metadata_filter is not None:
            vector_configuration["filter"] = metadata_filter
        request = {
            "knowledgeBaseId": self.knowledge_base_id,
            "retrievalQuery": {"text": query},
            "retrievalConfiguration": {
                "vectorSearchConfiguration": vector_configuration,
            },
        }

        try:
            response = self.client.retrieve(**request)
        except (NoCredentialsError, PartialCredentialsError) as exc:
            raise BedrockConfigurationError(
                "AWS credentials were not found. Configure boto3's standard "
                "credential provider chain (for example AWS profile, SSO, "
                "environment credentials, or an IAM role)."
            ) from exc
        except ClientError as exc:
            error = exc.response.get("Error", {})
            code = error.get("Code", "ClientError")
            message = error.get("Message", str(exc))
            raise BedrockRetrievalError(
                f"Bedrock Knowledge Bases Retrieve failed ({code}): {message}"
            ) from exc
        except BotoCoreError as exc:
            raise BedrockRetrievalError(
                f"Bedrock Knowledge Bases Retrieve failed: {exc}"
            ) from exc

        return [
            _to_retrieval_result(item, rank)
            for rank, item in enumerate(response.get("retrievalResults", []), start=1)
        ]


def _to_retrieval_result(item: dict[str, Any], rank: int) -> RetrievalResult:
    metadata = dict(item.get("metadata") or {})
    document_id = str(item.get("documentId") or metadata.get("document_id") or "")
    section_value = metadata.get("section") or metadata.get("heading")
    score = item.get("score")
    provider_score = float(score) if score is not None else None
    metadata["retrieval_provider"] = RetrievalProvider.BEDROCK_KB.value

    return RetrievalResult(
        chunk_id=f"bedrock-{document_id or 'result'}-{rank:04d}",
        document_id=document_id,
        section=str(section_value) if section_value is not None else None,
        source=_source_from_location(item.get("location") or {}),
        text=_text_from_content(item.get("content") or {}),
        similarity_score=None,
        metadata=metadata,
        provider=RetrievalProvider.BEDROCK_KB,
        rank=rank,
        provider_score=provider_score,
        origin=RetrievalOrigin.BEDROCK,
    )


def _text_from_content(content: dict[str, Any]) -> str:
    if content.get("text"):
        return str(content["text"])
    if content.get("audio", {}).get("transcription"):
        return str(content["audio"]["transcription"])
    if content.get("video", {}).get("summary"):
        return str(content["video"]["summary"])

    columns = [
        f"{column.get('columnName', 'column')}: {column.get('columnValue', '')}"
        for column in content.get("row") or []
    ]
    return ", ".join(columns)


def _source_from_location(location: dict[str, Any]) -> str:
    for location_name in (
        "s3Location",
        "webLocation",
        "confluenceLocation",
        "sharePointLocation",
        "salesforceLocation",
        "oneDriveLocation",
        "googleDriveLocation",
        "kendraDocumentLocation",
        "customDocumentLocation",
        "sqlLocation",
    ):
        value = location.get(location_name)
        if not isinstance(value, dict):
            continue
        for key in ("uri", "url", "id", "query"):
            if value.get(key):
                return str(value[key])
    return json.dumps(location, sort_keys=True) if location else ""

"""Optional real OpenAI Responses API smoke test."""

import os

import pytest

from design_automation.generation import OpenAIClient, OpenAIConfig
from design_automation.models import LLMRequest


pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.getenv("RUN_OPENAI_INTEGRATION") != "1" or not os.getenv("OPENAI_API_KEY"),
    reason="Set RUN_OPENAI_INTEGRATION=1 and OPENAI_API_KEY to call OpenAI",
)
def test_openai_responses_api_live() -> None:
    client = OpenAIClient(
        OpenAIConfig(
            api_key=os.environ["OPENAI_API_KEY"],
            model=os.getenv("OPENAI_MODEL", "gpt-5.6-terra"),
            timeout_seconds=60,
            max_output_tokens=200,
            reasoning_effort="low",
        )
    )

    response = client.generate(
        LLMRequest(
            system_prompt="Answer only from the supplied statement.",
            user_prompt=(
                "Statement: Order Service owns refund eligibility. "
                "Write one short impact-analysis sentence."
            ),
            requested_section="impact_analysis",
        )
    )

    assert response.provider == "openai"
    assert response.text.strip()
    assert response.provider_request_id

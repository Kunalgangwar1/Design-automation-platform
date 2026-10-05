"""Optional real Gemini API smoke test."""

import os

import pytest

from design_automation.generation import GeminiClient, GeminiConfig
from design_automation.models import LLMRequest


pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.getenv("RUN_GEMINI_INTEGRATION") != "1" or not os.getenv("GEMINI_API_KEY"),
    reason="Set RUN_GEMINI_INTEGRATION=1 and GEMINI_API_KEY to call Gemini",
)
def test_gemini_generate_content_live() -> None:
    client = GeminiClient(
        GeminiConfig(
            api_key=os.environ["GEMINI_API_KEY"],
            model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite"),
            timeout_seconds=60,
            max_output_tokens=200,
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

    assert response.provider == "gemini"
    assert response.text.strip()

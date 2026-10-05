"""Construct configured LLM clients in explicit fallback order."""

from collections.abc import Sequence

from design_automation.core import LLM_PROVIDER_ORDER
from design_automation.generation.gemini_client import GeminiClient
from design_automation.generation.llm import LLMClient, LLMConfigurationError
from design_automation.generation.openai_client import OpenAIClient


def build_llm_clients(
    provider_order: Sequence[str] = LLM_PROVIDER_ORDER,
) -> list[LLMClient]:
    """Build known providers in their configured primary-to-fallback order."""

    clients: list[LLMClient] = []
    seen: set[str] = set()
    for raw_name in provider_order:
        name = raw_name.strip().casefold()
        if not name:
            continue
        if name in seen:
            raise LLMConfigurationError(f"Duplicate LLM provider: {name}")
        seen.add(name)
        if name == "openai":
            clients.append(OpenAIClient())
        elif name == "gemini":
            clients.append(GeminiClient())
        else:
            raise LLMConfigurationError(f"Unsupported LLM provider: {name}")

    if not clients:
        raise LLMConfigurationError("LLM_PROVIDER_ORDER cannot be empty")
    return clients

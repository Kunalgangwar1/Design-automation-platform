"""Provider-independent generation, validation, retry, and persistence flow."""

import logging
import random
from collections.abc import Callable, Sequence
from time import perf_counter, sleep
from typing import Protocol

from design_automation.core import (
    LLM_MAX_RETRY_ATTEMPTS,
    LLM_RETRY_DELAY_SECONDS,
    LLM_RETRY_JITTER_SECONDS,
    LLM_RETRY_MAX_DELAY_SECONDS,
)
from design_automation.core.observability import elapsed_ms, log_event
from design_automation.generation.llm import GenerationError, LLMClient
from design_automation.generation.validation import validate_generation
from design_automation.models import (
    GenerationRequest,
    GenerationRequestStatus,
    GenerationResult,
    GenerationRun,
    LLMRequest,
    LLMResponse,
    PreparedGenerationRequest,
)


logger = logging.getLogger(__name__)


class RequestStatusRepository(Protocol):
    def update_status(
        self,
        request_id: str,
        status: GenerationRequestStatus,
    ) -> GenerationRequest: ...


class RunRepository(Protocol):
    def create(
        self,
        generation_request_id: str,
        provider: str,
        model: str,
        prompt_name: str,
        prompt_version: int,
    ) -> GenerationRun: ...

    def mark_generated(
        self,
        run_id: str,
        response: LLMResponse,
    ) -> GenerationRun: ...

    def mark_failed(
        self,
        run_id: str,
        error_message: str,
        response: LLMResponse | None = None,
    ) -> GenerationRun: ...


class GenerationService:
    """Consume a Day-6 request and coordinate generation only."""

    def __init__(
        self,
        llm_clients: LLMClient | Sequence[LLMClient],
        request_repository: RequestStatusRepository,
        run_repository: RunRepository,
        max_retry_attempts: int = LLM_MAX_RETRY_ATTEMPTS,
        retry_delay_seconds: float = LLM_RETRY_DELAY_SECONDS,
        retry_jitter_seconds: float = LLM_RETRY_JITTER_SECONDS,
        retry_max_delay_seconds: float = LLM_RETRY_MAX_DELAY_SECONDS,
        sleep_function: Callable[[float], None] = sleep,
        random_function: Callable[[], float] = random.random,
    ) -> None:
        if max_retry_attempts < 0:
            raise ValueError("max_retry_attempts cannot be negative")
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds cannot be negative")
        if retry_jitter_seconds < 0:
            raise ValueError("retry_jitter_seconds cannot be negative")
        if retry_max_delay_seconds < 0:
            raise ValueError("retry_max_delay_seconds cannot be negative")
        if isinstance(llm_clients, Sequence):
            clients = tuple(llm_clients)
        else:
            clients = (llm_clients,)
        if not clients:
            raise ValueError("At least one LLM client is required")
        self._llm_clients = clients
        self._requests = request_repository
        self._runs = run_repository
        self._max_retry_attempts = max_retry_attempts
        self._retry_delay_seconds = retry_delay_seconds
        self._retry_jitter_seconds = retry_jitter_seconds
        self._retry_max_delay_seconds = retry_max_delay_seconds
        self._sleep = sleep_function
        self._random = random_function

    def generate(
        self,
        prepared: PreparedGenerationRequest,
    ) -> GenerationResult:
        if prepared.status is not GenerationRequestStatus.CONTEXT_READY:
            raise ValueError("Prepared request must have status CONTEXT_READY")

        request = LLMRequest(
            system_prompt=prepared.rendered_prompt.system_prompt,
            user_prompt=prepared.rendered_prompt.task_prompt,
            requested_section=prepared.requested_section,
        )

        for index, client in enumerate(self._llm_clients):
            persistence_started = perf_counter()
            run = self._runs.create(
                prepared.request_id,
                client.provider,
                client.model,
                prepared.prompt_name,
                prepared.prompt_version,
            )
            log_event(
                logger,
                logging.INFO,
                "provider_attempt_started",
                generation_request_id=prepared.request_id,
                generation_run_id=run.id,
                provider=client.provider,
                model=client.model,
                provider_position=index + 1,
                persistence_latency_ms=elapsed_ms(persistence_started),
            )
            generation_started = perf_counter()
            try:
                response = self._generate_with_retries(client, request)
            except GenerationError as exc:
                persistence_started = perf_counter()
                self._runs.mark_failed(
                    run.id,
                    f"Provider failure category: {type(exc).__name__}",
                )
                has_fallback = index + 1 < len(self._llm_clients)
                log_event(
                    logger,
                    logging.WARNING,
                    "provider_attempt_failed",
                    generation_request_id=prepared.request_id,
                    generation_run_id=run.id,
                    provider=client.provider,
                    model=client.model,
                    latency_ms=elapsed_ms(generation_started),
                    persistence_latency_ms=elapsed_ms(persistence_started),
                    failure_category=type(exc).__name__,
                    retryable=exc.retryable,
                    fallback_available=has_fallback,
                )
                if exc.retryable and has_fallback:
                    next_client = self._llm_clients[index + 1]
                    log_event(
                        logger,
                        logging.WARNING,
                        "provider_fallback_started",
                        generation_request_id=prepared.request_id,
                        from_provider=client.provider,
                        to_provider=next_client.provider,
                        failure_category=type(exc).__name__,
                        fallback_occurred=True,
                    )
                    continue
                persistence_started = perf_counter()
                self._requests.update_status(
                    prepared.request_id,
                    GenerationRequestStatus.FAILED,
                )
                log_event(
                    logger,
                    logging.ERROR,
                    "generation_failed",
                    generation_request_id=prepared.request_id,
                    provider=client.provider,
                    failure_category=type(exc).__name__,
                    persistence_latency_ms=elapsed_ms(persistence_started),
                )
                raise

            log_event(
                logger,
                logging.INFO,
                "llm_generation_completed",
                generation_request_id=prepared.request_id,
                generation_run_id=run.id,
                provider=response.provider,
                model=response.model,
                stage="llm_generation",
                latency_ms=elapsed_ms(generation_started),
                provider_latency_ms=response.latency_ms,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                fallback_occurred=index > 0,
            )
            return self._finalize(prepared, run, response)

        raise RuntimeError("LLM provider loop completed without a result")

    def _generate_with_retries(
        self,
        client: LLMClient,
        request: LLMRequest,
    ) -> LLMResponse:
        retries_used = 0
        while True:
            try:
                return client.generate(request)
            except GenerationError as exc:
                if not exc.retryable or retries_used >= self._max_retry_attempts:
                    raise
                exponential_delay = self._retry_delay_seconds * (2**retries_used)
                delay_with_jitter = (
                    exponential_delay
                    + self._random() * self._retry_jitter_seconds
                )
                delay = min(delay_with_jitter, self._retry_max_delay_seconds)
                retries_used += 1
                log_event(
                    logger,
                    logging.WARNING,
                    "provider_retry_scheduled",
                    provider=client.provider,
                    model=client.model,
                    attempt=retries_used + 1,
                    failure_category=type(exc).__name__,
                    backoff_seconds=round(delay, 3),
                )
                if delay:
                    self._sleep(delay)

    def _finalize(
        self,
        prepared: PreparedGenerationRequest,
        run: GenerationRun,
        response: LLMResponse,
    ) -> GenerationResult:
        validation_started = perf_counter()
        validation = validate_generation(
            response.text,
            prepared.requested_section,
            prepared.context,
        )
        log_event(
            logger,
            logging.INFO if validation.valid else logging.WARNING,
            "generation_validation_completed",
            generation_request_id=prepared.request_id,
            generation_run_id=run.id,
            stage="validation",
            status="success" if validation.valid else "failed",
            latency_ms=elapsed_ms(validation_started),
            validation_error_count=len(validation.errors),
            validation_warning_count=len(validation.warnings),
        )
        persistence_started = perf_counter()
        if not validation.valid:
            self._runs.mark_failed(
                run.id,
                "; ".join(validation.errors),
                response,
            )
            request_status = GenerationRequestStatus.FAILED
        else:
            self._runs.mark_generated(run.id, response)
            request_status = GenerationRequestStatus.GENERATED

        status = self._requests.update_status(
            prepared.request_id,
            request_status,
        ).status
        log_event(
            logger,
            logging.INFO if validation.valid else logging.ERROR,
            "generation_persistence_completed",
            generation_request_id=prepared.request_id,
            generation_run_id=run.id,
            stage="persistence",
            status=status.value,
            latency_ms=elapsed_ms(persistence_started),
        )
        return GenerationResult(
            generation_request_id=prepared.request_id,
            generation_run_id=run.id,
            requested_section=prepared.requested_section,
            provider=response.provider,
            model=response.model,
            generated_text=response.text,
            prompt_name=prepared.prompt_name,
            prompt_version=prepared.prompt_version,
            latency_ms=response.latency_ms,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            provider_request_id=response.provider_request_id,
            validation=validation,
            status=status,
            operational_warnings=list(prepared.operational_warnings),
        )

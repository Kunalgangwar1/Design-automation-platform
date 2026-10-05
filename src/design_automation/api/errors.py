"""Map application and infrastructure failures to safe HTTP responses."""

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from neo4j.exceptions import DriverError, Neo4jError
from sqlalchemy.exc import SQLAlchemyError

from design_automation.generation import (
    GenerationError,
    GenerationNotFoundError,
    PromptTemplateNotFoundError,
)
from design_automation.core.observability import get_request_id, log_event


logger = logging.getLogger(__name__)


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None) or get_request_id()


def _response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    request_id = _request_id(request)
    return JSONResponse(
        status_code=status_code,
        headers={"X-Request-ID": request_id} if request_id else None,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id,
            }
        },
    )


async def generation_not_found_handler(
    request: Request,
    __: GenerationNotFoundError,
) -> JSONResponse:
    log_event(
        logger,
        logging.INFO,
        "api_error",
        code="generation_not_found",
        request_id=_request_id(request),
    )
    return _response(
        request,
        status.HTTP_404_NOT_FOUND,
        "generation_not_found",
        "The requested generation does not exist.",
    )


async def generation_error_handler(
    request: Request,
    error: GenerationError,
) -> JSONResponse:
    log_event(
        logger,
        logging.WARNING,
        "api_error",
        code="generation_provider_error",
        error_type=type(error).__name__,
        retryable=error.retryable,
        request_id=_request_id(request),
    )
    if error.retryable:
        return _response(
            request,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "generation_provider_unavailable",
            "The generation provider is temporarily unavailable.",
        )
    return _response(
        request,
        status.HTTP_502_BAD_GATEWAY,
        "generation_provider_error",
        "The generation provider could not complete the request.",
    )


async def prompt_template_error_handler(
    request: Request,
    __: PromptTemplateNotFoundError,
) -> JSONResponse:
    log_event(
        logger,
        logging.ERROR,
        "api_error",
        code="prompt_template_unavailable",
        request_id=_request_id(request),
    )
    return _response(
        request,
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "prompt_template_unavailable",
        "The requested generation template is unavailable.",
    )


async def dependency_error_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    log_event(
        logger,
        logging.ERROR,
        "api_error",
        code="dependency_unavailable",
        error_type=type(error).__name__,
        request_id=_request_id(request),
    )
    return _response(
        request,
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "dependency_unavailable",
        "A required application dependency is unavailable.",
    )


async def unexpected_error_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    log_event(
        logger,
        logging.ERROR,
        "api_error",
        code="internal_error",
        error_type=type(error).__name__,
        request_id=_request_id(request),
    )
    return _response(
        request,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "internal_error",
        "An unexpected internal error occurred.",
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(
        GenerationNotFoundError,
        generation_not_found_handler,
    )
    app.add_exception_handler(GenerationError, generation_error_handler)
    app.add_exception_handler(
        PromptTemplateNotFoundError,
        prompt_template_error_handler,
    )
    app.add_exception_handler(SQLAlchemyError, dependency_error_handler)
    app.add_exception_handler(Neo4jError, dependency_error_handler)
    app.add_exception_handler(DriverError, dependency_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)

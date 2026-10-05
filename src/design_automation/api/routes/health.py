"""Cheap liveness plus dependency readiness endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from design_automation.api.dependencies import ReadinessStatus, get_readiness_status
from design_automation.api.schemas import (
    HealthResponse,
    ReadinessCheckResponse,
    ReadinessResponse,
)


router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse}},
)
def readiness(
    response: Response,
    readiness_status: Annotated[ReadinessStatus, Depends(get_readiness_status)],
) -> ReadinessResponse:
    if not readiness_status.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ready" if readiness_status.ready else "not_ready",
        checks={
            name: ReadinessCheckResponse(
                status="ok" if available else "unavailable"
            )
            for name, available in readiness_status.checks.items()
        },
    )

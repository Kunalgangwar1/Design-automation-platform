"""Thin HTTP routes for generation creation and retrieval."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, status

from design_automation.api.dependencies import get_generation_application_service
from design_automation.api.schemas import (
    ErrorResponse,
    GenerationCreateRequest,
    GenerationResponse,
)
from design_automation.generation import GenerationApplicationService


router = APIRouter(prefix="/generations", tags=["generations"])
GenerationServiceDependency = Annotated[
    GenerationApplicationService,
    Depends(get_generation_application_service),
]


@router.post(
    "",
    response_model=GenerationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def create_generation(
    payload: GenerationCreateRequest,
    service: GenerationServiceDependency,
) -> GenerationResponse:
    result = service.generate(
        payload.requirement,
        payload.section,
        payload.graph_hops,
    )
    return GenerationResponse.from_result(result)


@router.get(
    "/{request_id}",
    response_model=GenerationResponse,
    responses={404: {"model": ErrorResponse}},
)
def get_generation(
    request_id: Annotated[str, Path(min_length=1, max_length=100)],
    service: GenerationServiceDependency,
) -> GenerationResponse:
    return GenerationResponse.from_persisted(
        service.get_generation(request_id)
    )

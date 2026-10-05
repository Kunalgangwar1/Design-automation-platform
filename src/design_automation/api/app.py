"""FastAPI application factory and process-wide resource lifespan."""

from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from design_automation.api.middleware import RequestContextMiddleware
from design_automation.api.dependencies import (
    ApplicationResources,
    build_application_resources,
)
from design_automation.api.errors import register_error_handlers
from design_automation.api.routes.generations import router as generations_router
from design_automation.api.routes.health import router as health_router
from design_automation.core import LOG_LEVEL
from design_automation.core.observability import configure_logging


ResourcesFactory = Callable[[], ApplicationResources | None]


def create_app(
    resources_factory: ResourcesFactory = build_application_resources,
) -> FastAPI:
    """Create the HTTP application with replaceable resource construction."""

    configure_logging(LOG_LEVEL)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        resources = resources_factory()
        application.state.resources = resources
        try:
            yield
        finally:
            if resources is not None:
                resources.close()

    application = FastAPI(
        title="Design Automation Platform",
        version="0.1.0",
        description="Grounded Solution Design Document generation API",
        lifespan=lifespan,
    )
    application.add_middleware(RequestContextMiddleware)
    application.include_router(health_router)
    application.include_router(generations_router, prefix="/api/v1")
    register_error_handlers(application)
    return application


app = create_app()

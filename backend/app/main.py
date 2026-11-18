import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import RequestResponseEndpoint

from app.api.errors import register_exception_handlers
from app.api.batches import router as batches_router
from app.api.health import router as health_router
from app.api.policies import router as policies_router
from app.api.workspace import router as workspace_router
from app.config import Settings, get_settings
from app.database.init_db import initialize_database
from app.utils.logging import configure_logging, new_request_id, request_id_context

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings.log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if app_settings.auto_create_tables:
            initialize_database()
        yield

    application = FastAPI(
        title=app_settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.middleware("http")
    async def request_logging(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = new_request_id()
        token = request_id_context.set(request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            logger.info(
                "Request completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                },
            )
            return response
        finally:
            request_id_context.reset(token)

    application.include_router(health_router, prefix=app_settings.api_prefix, tags=["health"])
    application.include_router(
        policies_router,
        prefix=app_settings.api_prefix,
        tags=["policy management"],
    )
    application.include_router(
        batches_router,
        prefix=app_settings.api_prefix,
        tags=["batch processing"],
    )
    application.include_router(
        workspace_router,
        prefix=app_settings.api_prefix,
        tags=["procurement workspace"],
    )
    register_exception_handlers(application)
    return application


app = create_app()

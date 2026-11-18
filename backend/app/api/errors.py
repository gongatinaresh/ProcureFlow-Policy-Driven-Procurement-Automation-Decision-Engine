import logging
from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.schemas.common import ErrorDetail, ErrorResponse
from app.utils.logging import request_id_context

logger = logging.getLogger(__name__)


def _error_response(
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    details: Sequence[dict[str, Any]] | dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    payload = ErrorResponse(
        error=ErrorDetail(
            code=code,
            message=message,
            details=list(details) if isinstance(details, Sequence) and not isinstance(details, (str, bytes)) else details,
            request_id=request_id,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exception: RequestValidationError
    ) -> JSONResponse:
        errors = [
            {
                "location": [str(part) for part in error["loc"]],
                "message": error["msg"],
                "type": error["type"],
            }
            for error in exception.errors()
        ]
        return _error_response(
            422,
            "request_validation_error",
            "The request did not match the expected schema.",
            request_id_context.get(),
            errors,
        )

    @app.exception_handler(IntegrityError)
    async def integrity_exception_handler(
        request: Request, exception: IntegrityError
    ) -> JSONResponse:
        logger.info("Database constraint rejected a request", exc_info=exception)
        return _error_response(
            409,
            "database_conflict",
            "The request conflicts with existing data or a database constraint.",
            request_id_context.get(),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exception: StarletteHTTPException
    ) -> JSONResponse:
        detail = exception.detail
        message = detail if isinstance(detail, str) else "The request could not be completed."
        return _error_response(
            exception.status_code,
            "http_error",
            message,
            request_id_context.get(),
            headers=exception.headers,
        )

    @app.exception_handler(Exception)
    async def unexpected_exception_handler(
        request: Request, exception: Exception
    ) -> JSONResponse:
        logger.exception("Unhandled request error")
        return _error_response(
            500,
            "internal_server_error",
            "An unexpected server error occurred.",
            request_id_context.get(),
        )

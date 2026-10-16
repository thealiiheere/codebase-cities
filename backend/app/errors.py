"""Application errors and the handlers that render every error as `{"error": {...}}`."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.schemas.errors import ErrorBody, ErrorResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base for errors that map to a specific HTTP status and error code."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "service_unavailable"


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message))
    return JSONResponse(status_code=status_code, content=body.model_dump())


async def _handle_app_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return error_response(exc.status_code, exc.code, exc.message)


_HTTP_ERROR_CODES = {404: "not_found", 405: "method_not_allowed"}


async def _handle_http_error(_: Request, exc: Exception) -> JSONResponse:
    # Covers framework-raised errors such as unknown routes (404) and wrong methods (405).
    assert isinstance(exc, StarletteHTTPException)
    code = _HTTP_ERROR_CODES.get(exc.status_code, "http_error")
    return error_response(exc.status_code, code, str(exc.detail))


async def _handle_validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Report the first problem only, e.g. "query.until: Input should be a valid date".
    first = exc.errors()[0] if exc.errors() else {}
    location = ".".join(str(part) for part in first.get("loc", ()))
    detail = first.get("msg", "invalid request")
    message = f"{location}: {detail}" if location else detail
    return error_response(422, "validation_error", message)


async def _handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error", exc_info=exc)
    return error_response(500, "internal_error", "An unexpected error occurred.")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)

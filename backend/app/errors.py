"""Centralised errors with a single response shape for the whole API.

Shape: {"error": {"code": str, "message": str, "details": any}}

Purpose: clients (frontend, POS) only need to handle one error format. Services raise
`AppError(code, message, status)` and do not need to know about the HTTP response.

Relations: the handlers are registered in app/main.py. Used by services/routers in all tasks
(e.g. INSUFFICIENT_STOCK, IDEMPOTENCY_KEY_REUSED, ODOO_UNAVAILABLE).
"""
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("app.errors")


class AppError(Exception):
    """Known business/operational error, with a machine code and an HTTP status."""

    def __init__(self, code: str, message: str, status: int, details: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details


def _body(code: str, message: str, details: Any = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def register_error_handlers(app: FastAPI) -> None:
    """Attach all error handlers to the application."""

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(_body(exc.code, exc.message, exc.details), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        # 422: invalid input. jsonable_encoder so that Pydantic error details can be serialised.
        return JSONResponse(
            _body("VALIDATION_ERROR", "Request validation failed", jsonable_encoder(exc.errors())),
            status_code=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        # E.g. a missing route (404) or a wrong method (405) still uses the standard error shape.
        code = "NOT_FOUND" if exc.status_code == 404 else f"HTTP_{exc.status_code}"
        return JSONResponse(_body(code, str(exc.detail)), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception):
        # Unexpected error: the detail is written to the log, the client receives only a generic message.
        log.exception("unhandled_error")
        return JSONResponse(_body("INTERNAL_ERROR", "Internal server error"), status_code=500)

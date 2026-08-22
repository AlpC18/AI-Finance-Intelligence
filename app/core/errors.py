"""Shared exceptions and FastAPI handlers."""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Base application error carrying an HTTP status, message and metric reason.

    ``reason`` is a stable, low-cardinality machine label (``risk_halt``,
    ``notional``, ``buying_power``, ...) used for Prometheus labels and span
    attributes. It exists so operator metrics never depend on parsing the
    user-facing ``message``, which is localized and free to change.
    """

    def __init__(
        self, message: str, status_code: int = 400, reason: str = "app_error"
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.reason = reason


class ProviderError(AppError):
    """Raised when an external data provider fails."""

    def __init__(self, message: str, reason: str = "provider") -> None:
        super().__init__(message, status_code=502, reason=reason)


class NotFoundError(AppError):
    def __init__(self, message: str, reason: str = "not_found") -> None:
        super().__init__(message, status_code=404, reason=reason)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.message},
        )

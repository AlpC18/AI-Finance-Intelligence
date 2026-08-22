"""FastAPI app factory: observability, config preflight, auth, rate limiting, alerts."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

import app.core.metrics  # noqa: F401 - register custom counters on import
from app.api.router import api_router
from app.core.config import ConfigError, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestIDMiddleware
from app.core.rate_limit import limiter
from app.core.tracing import configure_tracing
from app.db.database import init_db

configure_logging()
logger = get_logger("startup")
_WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def _preflight() -> None:
    settings = get_settings()
    errors = settings.production_config_errors()
    if errors:
        for err in errors:
            logger.critical("fatal_config", error=err)
        raise ConfigError(
            "Refusing to boot: unsafe production configuration ("
            + "; ".join(errors)
            + ")."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _preflight()
    init_db()

    from app.core.deps import get_ws_broadcaster

    broadcaster = get_ws_broadcaster()
    await broadcaster.start()  # subscribe this worker to cross-worker WS alerts

    scheduler = None
    settings = get_settings()
    if getattr(app.state, "enable_scheduler", True) and settings.alerts_enabled:
        from app.core.scheduler import build_scheduler

        scheduler = build_scheduler(settings)
        scheduler.start()  # AsyncIOScheduler: runs on this event loop, non-blocking
        app.state.scheduler = scheduler
        logger.info("alert_engine_started", interval_minutes=settings.alert_interval_minutes)
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)
            logger.info("alert_engine_stopped")
        await broadcaster.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="AI Finance Intelligence", version="0.5.0", lifespan=lifespan)
    app.state.enable_scheduler = True
    app.state.limiter = limiter

    # Observability: request-id logging + Prometheus /metrics + OTel traces.
    app.add_middleware(RequestIDMiddleware)
    Instrumentator().instrument(app).expose(
        app, endpoint="/metrics", include_in_schema=False
    )
    configure_tracing(app, get_settings())  # no-op unless OTEL_ENABLED

    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    register_error_handlers(app)
    app.include_router(api_router)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(_WEB_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=_WEB_DIR), name="static")
    return app


app = create_app()

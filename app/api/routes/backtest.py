"""Backtesting endpoints — replay historical bars, and keep what came back.

Route order matters below: ``/runs/compare`` is declared before ``/runs/{run_id}``
so the literal path can never be shadowed by the parameterised one.
"""
from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlmodel import Session

from app.core.auth import get_current_user
from app.core.deps import get_backtest_service
from app.core.errors import AppError
from app.core.rate_limit import AI_RATE_LIMIT, limiter
from app.db.database import get_session
from app.models.backtest import (
    BacktestComparison,
    BacktestReport,
    BacktestRequest,
    BacktestRunPage,
)
from app.models.user import User
from app.services.backtest_service import BacktestService

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

# Bounded so one request cannot ask for an unbounded IN(...) of run ids.
_MAX_COMPARE = 10


@router.post("/run", response_model=BacktestReport)
@limiter.limit(AI_RATE_LIMIT)
async def run_backtest(
    request: Request,
    data: BacktestRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: BacktestService = Depends(get_backtest_service),
) -> BacktestReport:
    """Historical simulation: metrics summary + equity curve for the symbol.

    The run is saved and the report comes back carrying its ``id``, so a result
    worth revisiting does not have to be re-simulated to be seen again.
    """
    return await service.run_and_save(session, user.id, data)


@router.get("/runs", response_model=BacktestRunPage)
def list_runs(
    response: Response,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    symbol: str | None = Query(default=None, max_length=20),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: BacktestService = Depends(get_backtest_service),
) -> BacktestRunPage:
    """Saved runs, newest first, without the equity curve."""
    page = service.list_runs(
        session, user.id, limit=limit, offset=offset, symbol=symbol
    )
    response.headers["X-Total-Count"] = str(page.total)
    return page


@router.get("/runs/compare", response_model=BacktestComparison)
def compare_runs(
    ids: str = Query(description="Comma-separated run ids, e.g. 4,7,9"),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: BacktestService = Depends(get_backtest_service),
) -> BacktestComparison:
    """Several runs side by side, plus the leader on each metric."""
    return service.compare(session, user.id, _parse_ids(ids))


@router.get("/runs/{run_id}", response_model=BacktestReport)
def get_run(
    run_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: BacktestService = Depends(get_backtest_service),
) -> BacktestReport:
    """One stored run in full, curve and trades included."""
    return service.get_run(session, user.id, run_id)


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_run(
    run_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: BacktestService = Depends(get_backtest_service),
) -> Response:
    service.delete_run(session, user.id, run_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _parse_ids(raw: str) -> list[int]:
    """Parse the id list at the boundary, rejecting anything unusable.

    Deliberately strict rather than skipping bad entries: a comparison that
    quietly drops what it could not read is worse than one that refuses.
    """
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        raise AppError("En az bir kayit id'si gerekli.", status_code=422,
                       reason="compare_ids")
    if len(parts) > _MAX_COMPARE:
        raise AppError(
            f"En fazla {_MAX_COMPARE} kayit karsilastirilabilir.",
            status_code=422, reason="compare_ids",
        )
    try:
        ids = [int(p) for p in parts]
    except ValueError as exc:
        raise AppError("Kayit id'leri sayisal olmalidir.", status_code=422,
                       reason="compare_ids") from exc
    if len(set(ids)) != len(ids):
        raise AppError("Ayni kayit birden fazla kez verilemez.", status_code=422,
                       reason="compare_ids")
    return ids

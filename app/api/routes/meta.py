"""Machine-readable API contracts for the frontend (WS frames + trade/backtest I/O)."""
from fastapi import APIRouter

from app.models.backtest import BacktestReport, BacktestRequest
from app.models.broker import TradeRequest
from app.models.deck import ApiContracts, WsContract
from app.models.risk import KillSwitchStatus
from app.models.ws import (
    WsAlertFrame,
    WsEndFrame,
    WsErrorFrame,
    WsStartFrame,
    WsTokenFrame,
)

router = APIRouter(prefix="/api/meta", tags=["meta"])


@router.get("/contracts", response_model=ApiContracts)
def contracts() -> ApiContracts:
    """Typed JSON contracts so the UI can bind to a single source of truth."""
    return ApiContracts(
        ws=WsContract(),
        ws_frames={
            "start": WsStartFrame.model_json_schema(),
            "token": WsTokenFrame.model_json_schema(),
            "end": WsEndFrame.model_json_schema(),
            "error": WsErrorFrame.model_json_schema(),
            "alert": WsAlertFrame.model_json_schema(),
        },
        kill_switch=KillSwitchStatus.model_json_schema(),
        trade_request=TradeRequest.model_json_schema(),
        backtest_request=BacktestRequest.model_json_schema(),
        backtest_report=BacktestReport.model_json_schema(),
    )

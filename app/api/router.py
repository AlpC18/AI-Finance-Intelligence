"""Aggregate all API routers (REST + WebSocket)."""
from fastapi import APIRouter

from app.api import websockets
from app.api.routes import (
    alerts, auth, backtest, events, health, market, meta, news, portfolio, trade,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(market.router)
api_router.include_router(news.router)
api_router.include_router(portfolio.router)
api_router.include_router(alerts.router)
api_router.include_router(trade.router)
api_router.include_router(backtest.router)
api_router.include_router(meta.router)
api_router.include_router(events.router)
api_router.include_router(websockets.router)  # /ws/insights/{symbol}

"""Aggregate all API routers (REST + WebSocket)."""
from fastapi import APIRouter

from app.api import websockets
from app.api.routes import (
    activity, admin, advanced_quant, alerts, assets, auth, automations, backtest, consumer, deep_event_intelligence, developer, equity_catalyst, events, health, macro_news, market, marketplace, mcp_server, meta, multi_agent, news, options, paper, portfolio, rag, trade, watchlist,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(market.router)
api_router.include_router(multi_agent.router)
api_router.include_router(advanced_quant.router)
api_router.include_router(macro_news.router)
api_router.include_router(equity_catalyst.router)
api_router.include_router(deep_event_intelligence.router)
api_router.include_router(mcp_server.router)
api_router.include_router(news.router)
api_router.include_router(portfolio.router)
api_router.include_router(alerts.router)
api_router.include_router(activity.router)
api_router.include_router(assets.router)
api_router.include_router(developer.router)
api_router.include_router(consumer.router)
api_router.include_router(admin.router)
api_router.include_router(watchlist.router)
api_router.include_router(paper.router)
api_router.include_router(automations.router)
api_router.include_router(trade.router)
api_router.include_router(backtest.router)
api_router.include_router(options.router)
api_router.include_router(rag.router)
api_router.include_router(marketplace.router)
api_router.include_router(meta.router)
api_router.include_router(events.router)
api_router.include_router(websockets.router)  # /ws/insights/{symbol}

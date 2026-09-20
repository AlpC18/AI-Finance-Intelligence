"""Options and Derivatives pricing endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.routes.auth import get_current_user
from app.core.config import Settings, get_settings
from app.models.options import (
    OptionChain,
    OptionsStrategyPayoff,
    OptionsStrategyRequest,
)
from app.models.user import User
from app.services.market_service import MarketService
from app.services.options_pricing_service import OptionsPricingService

router = APIRouter(prefix="/options", tags=["options-derivatives"])


@router.get("/chain/{symbol}", response_model=OptionChain)
async def get_symbol_option_chain(
    symbol: str,
    iv: float = Query(default=0.35, ge=0.01, le=5.0),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> OptionChain:
    """Fetch live option chain with calculated BSM Greeks for calls and puts."""
    market_svc = MarketService(settings)
    try:
        quote = await market_svc.get_quote(symbol)
        price = float(quote.get("price") or quote.get("close") or 150.0)
    except Exception:
        price = 150.0

    svc = OptionsPricingService()
    return svc.generate_option_chain(symbol=symbol, underlying_price=price, iv=iv)


@router.post("/strategy-payoff", response_model=OptionsStrategyPayoff)
def calculate_strategy_payoff(
    request: OptionsStrategyRequest,
    current_user: User = Depends(get_current_user),
) -> OptionsStrategyPayoff:
    """Calculate multi-leg options strategy payoff curve, max profit/loss, and net Greeks."""
    svc = OptionsPricingService()
    return svc.calculate_strategy_payoff(request)


@router.get("/templates")
def get_strategy_templates():
    """Return standard options strategy templates (Covered Call, Iron Condor, etc.)."""
    return [
        {
            "name": "Covered Call",
            "type": "covered_call",
            "outlook": "Mildly Bullish / Neutral",
            "legs": [
                {"is_stock_leg": True, "action": "buy", "quantity": 100, "description": "Long 100 Shares"},
                {"option_type": "call", "action": "sell", "quantity": 1, "description": "Sell 1 OTM Call"},
            ],
        },
        {
            "name": "Protective Put",
            "type": "protective_put",
            "outlook": "Bullish with Downside Floor",
            "legs": [
                {"is_stock_leg": True, "action": "buy", "quantity": 100, "description": "Long 100 Shares"},
                {"option_type": "put", "action": "buy", "quantity": 1, "description": "Buy 1 OTM Put"},
            ],
        },
        {
            "name": "Iron Condor",
            "type": "iron_condor",
            "outlook": "Rangebound / Low Volatility",
            "legs": [
                {"option_type": "put", "action": "buy", "quantity": 1, "description": "Buy OTM Put (Wing)"},
                {"option_type": "put", "action": "sell", "quantity": 1, "description": "Sell OTM Put (Body)"},
                {"option_type": "call", "action": "sell", "quantity": 1, "description": "Sell OTM Call (Body)"},
                {"option_type": "call", "action": "buy", "quantity": 1, "description": "Buy OTM Call (Wing)"},
            ],
        },
        {
            "name": "Straddle",
            "type": "straddle",
            "outlook": "High Volatility Breakout",
            "legs": [
                {"option_type": "call", "action": "buy", "quantity": 1, "description": "Buy ATM Call"},
                {"option_type": "put", "action": "buy", "quantity": 1, "description": "Buy ATM Put"},
            ],
        },
    ]

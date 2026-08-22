"""slowapi limiter keyed by authenticated user (fair LLM-budget protection)."""
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from app.core.config import get_settings
from app.core.security import decode_token


def user_or_ip_key(request: Request) -> str:
    """Rate-limit key: per user_id when authenticated, else per client IP."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        payload = decode_token(auth[7:], get_settings())
        if payload and payload.get("type") == "access" and payload.get("sub"):
            return f"user:{payload['sub']}"
    return get_remote_address(request)


limiter = Limiter(key_func=user_or_ip_key)
AI_RATE_LIMIT = get_settings().rate_limit_ai
TRADE_RATE_LIMIT = get_settings().rate_limit_trade

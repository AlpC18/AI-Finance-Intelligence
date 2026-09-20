"""Read-only operator overview contracts; no user secrets are ever exposed."""
from pydantic import BaseModel


class AdminOverview(BaseModel):
    users: int
    orders: int
    active_api_keys: int
    activity_events: int
    trading_mode: str
    cache_backend: str

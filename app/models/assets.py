"""Asset-class metadata and conservative execution/risk characteristics."""
from typing import Literal

from pydantic import BaseModel

AssetClass = Literal["stock", "etf", "crypto", "option"]


class AssetProfile(BaseModel):
    symbol: str
    asset_class: AssetClass
    market_hours: str
    fractional_quantity: bool
    leverage_allowed: bool = False
    maximum_leverage: float = 1.0
    minimum_quantity: float
    note: str

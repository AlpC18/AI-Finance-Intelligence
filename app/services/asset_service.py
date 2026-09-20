"""Single classification seam for venue-neutral asset safety rules."""
from __future__ import annotations

import re

from app.models.assets import AssetProfile

_ETF = {"SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "ARKK", "TLT", "GLD", "XLF", "XLK"}
_OPTION = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")


class AssetService:
    def profile(self, symbol: str) -> AssetProfile:
        normalized = symbol.upper().strip()
        if normalized.endswith("-USD") or "/" in normalized:
            return AssetProfile(symbol=normalized, asset_class="crypto", market_hours="24/7",
                fractional_quantity=True, minimum_quantity=0.00000001,
                note="Crypto executes only through a paper-safe or explicitly live-enabled venue.")
        if _OPTION.fullmatch(normalized):
            return AssetProfile(symbol=normalized, asset_class="option", market_hours="US options session",
                fractional_quantity=False, minimum_quantity=1,
                note="Options require contract-aware validation before execution.")
        if normalized in _ETF:
            return AssetProfile(symbol=normalized, asset_class="etf", market_hours="US regular session",
                fractional_quantity=True, minimum_quantity=0.00000001,
                note="ETF risk limits use the same base equity controls unless overridden.")
        return AssetProfile(symbol=normalized, asset_class="stock", market_hours="US regular session",
            fractional_quantity=True, minimum_quantity=0.00000001,
            note="Equity risk limits apply; extended-hours execution is not assumed.")

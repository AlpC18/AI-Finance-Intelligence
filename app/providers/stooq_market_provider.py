"""Secondary market-data source (Stooq daily CSV) — free, keyless, pure async.

Used as the failover behind Yahoo so a single upstream outage never freezes
signal generation. Stooq has no fundamentals, so ``get_fundamentals`` returns an
empty ``Fundamentals`` (the app already treats fundamentals as best-effort).
"""
from __future__ import annotations

import csv
import io
from typing import Optional

import httpx
import pandas as pd
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.errors import ProviderError
from app.models.market import Fundamentals

_CSV = "https://stooq.com/q/d/l/"
_HEADERS = {"User-Agent": "Mozilla/5.0 (AI-Finance-Intelligence)"}


def _stooq_symbol(symbol: str) -> str:
    """Stooq wants lower-case tickers; bare US tickers take a '.us' suffix."""
    s = symbol.lower().strip()
    return s if "." in s or "-" in s else f"{s}.us"


class StooqMarketProvider:
    def __init__(
        self,
        timeout: float = 10.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._timeout = timeout
        self._transport = transport  # test seam (httpx.MockTransport)

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _fetch_csv(self, symbol: str) -> str:
        params = {"s": _stooq_symbol(symbol), "i": "d"}
        async with httpx.AsyncClient(
            timeout=self._timeout, headers=_HEADERS, transport=self._transport
        ) as client:
            resp = await client.get(_CSV, params=params)
            resp.raise_for_status()
            return resp.text

    async def get_history(self, symbol: str, period: str = "6mo") -> pd.DataFrame:
        try:
            body = await self._fetch_csv(symbol)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Stooq verisi alinamadi: {symbol}") from exc
        closes: list[float] = []
        for row in csv.DictReader(io.StringIO(body)):
            raw = row.get("Close")
            if raw in (None, "", "N/D"):
                continue
            try:
                closes.append(float(raw))
            except (TypeError, ValueError):
                continue
        if not closes:
            raise ProviderError(f"Stooq sembol icin veri yok: {symbol}")
        return pd.DataFrame({"Close": closes})

    async def get_price(self, symbol: str) -> float:
        hist = await self.get_history(symbol, period="5d")
        return float(hist["Close"].iloc[-1])

    async def get_fundamentals(self, symbol: str) -> Fundamentals:
        return Fundamentals()  # Stooq exposes no fundamentals

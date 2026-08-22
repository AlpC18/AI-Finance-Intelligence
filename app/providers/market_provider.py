"""Async market data via Yahoo chart JSON API (pure httpx, tenacity-backed)."""
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

_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_SUMMARY = "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
_MODULES = "summaryDetail,defaultKeyStatistics,financialData,calendarEvents"
_HEADERS = {"User-Agent": "Mozilla/5.0 (AI-Finance-Intelligence)"}
_RANGE = {"1mo": "1mo", "6mo": "6mo", "1y": "1y", "5d": "5d"}


class YahooMarketProvider:
    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def _fetch_chart(self, symbol: str, period: str) -> dict:
        params = {"range": _RANGE.get(period, "6mo"), "interval": "1d"}
        async with httpx.AsyncClient(timeout=self._timeout, headers=_HEADERS) as client:
            resp = await client.get(_BASE.format(symbol=symbol), params=params)
            resp.raise_for_status()
            return resp.json()

    async def get_history(self, symbol: str, period: str = "6mo") -> pd.DataFrame:
        try:
            data = await self._fetch_chart(symbol, period)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Market data alinamadi: {symbol}") from exc
        try:
            result = data["chart"]["result"][0]
            closes = result["indicators"]["quote"][0]["close"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"Sembol icin veri yok: {symbol}") from exc
        clean = [c for c in closes if c is not None]
        if not clean:
            raise ProviderError(f"Sembol icin veri yok: {symbol}")
        return pd.DataFrame({"Close": clean})

    async def get_price(self, symbol: str) -> float:
        hist = await self.get_history(symbol, period="5d")
        return float(hist["Close"].iloc[-1])

    async def get_fundamentals(self, symbol: str) -> Fundamentals:
        """Best-effort fundamentals via Yahoo quoteSummary. Empty on any failure."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout, headers=_HEADERS) as client:
                resp = await client.get(
                    _SUMMARY.format(symbol=symbol), params={"modules": _MODULES}
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError:
            return Fundamentals()  # degrade quietly; never block the signal path
        return _parse_fundamentals(data)


def _raw(node: dict, *path: str):
    """Walk nested Yahoo dicts and return the leaf `.raw` value, or None."""
    cur: object = node
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    if isinstance(cur, dict):
        return cur.get("raw")
    return cur


def _parse_fundamentals(data: dict) -> Fundamentals:
    try:
        result = data["quoteSummary"]["result"][0]
    except (KeyError, IndexError, TypeError):
        return Fundamentals()
    summary = result.get("summaryDetail", {}) or {}
    stats = result.get("defaultKeyStatistics", {}) or {}
    financial = result.get("financialData", {}) or {}
    calendar = result.get("calendarEvents", {}) or {}
    earnings_dates = _walk(calendar, "earnings", "earningsDate")
    earnings_date = None
    if isinstance(earnings_dates, list) and earnings_dates:
        first = earnings_dates[0]
        earnings_date = first.get("fmt") if isinstance(first, dict) else None
    return Fundamentals(
        pe_ratio=_num(_raw(summary, "trailingPE")),
        ev_ebitda=_num(_raw(stats, "enterpriseToEbitda")),
        debt_to_equity=_num(_raw(financial, "debtToEquity")),
        short_ratio=_num(_raw(summary, "shortRatio")),
        earnings_date=earnings_date,
    )


def _walk(node: dict, *path: str):
    cur: object = node
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _num(value: object):
    try:
        return round(float(value), 4) if value is not None else None
    except (TypeError, ValueError):
        return None

"""Async provider protocols — swap free/paid sources without touching services."""
from typing import Protocol

import pandas as pd

from app.models.market import Fundamentals
from app.models.news import Article


class MarketDataProvider(Protocol):
    async def get_history(self, symbol: str, period: str = "6mo") -> pd.DataFrame: ...
    async def get_price(self, symbol: str) -> float: ...
    async def get_fundamentals(self, symbol: str) -> Fundamentals: ...


class NewsProvider(Protocol):
    async def fetch(self, query: str, limit: int = 10) -> list[Article]: ...

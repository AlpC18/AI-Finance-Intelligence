"""Async financial news: httpx fetch (I/O) + feedparser parse (pure), tenacity-backed."""
from __future__ import annotations

from typing import Optional
from urllib.parse import quote_plus

import feedparser
import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.models.news import Article

_HEADERS = {"User-Agent": "Mozilla/5.0 (AI-Finance-Intelligence)"}
_YAHOO = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={q}&region=US&lang=en-US"
_GOOGLE = "https://news.google.com/rss/search?q={q}+stock&hl=en-US&gl=US&ceid=US:en"


class RssNewsProvider:
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
    async def _get(self, client: httpx.AsyncClient, url: str) -> bytes:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content

    async def fetch(self, query: str, limit: int = 10) -> list[Article]:
        q = quote_plus(query)
        articles: list[Article] = []
        async with httpx.AsyncClient(
            timeout=self._timeout, headers=_HEADERS, transport=self._transport
        ) as client:
            for url in (_YAHOO.format(q=q), _GOOGLE.format(q=q)):
                try:
                    raw = await self._get(client, url)
                except httpx.HTTPError:
                    continue  # tolerate one feed failing; try the next
                feed = feedparser.parse(raw)  # parse-only, no network
                for entry in feed.entries:
                    articles.append(
                        Article(
                            title=getattr(entry, "title", ""),
                            link=getattr(entry, "link", ""),
                            published=getattr(entry, "published", ""),
                            source=feed.feed.get("title", ""),
                        )
                    )
                    if len(articles) >= limit:
                        return articles
        return articles

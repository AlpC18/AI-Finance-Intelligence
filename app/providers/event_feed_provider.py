"""Multi-source event feeds (regulatory, macro, social) over async httpx + feedparser.

Every feed is fetched defensively and in isolation: a single feed failing (403,
timeout, malformed XML) is skipped, never aborting the aggregate.
"""
from __future__ import annotations

import feedparser
import httpx

_HEADERS = {"User-Agent": "Mozilla/5.0 (AI-Finance-Intelligence)"}

# (source_label, feed_url) grouped by the three ingestion domains.
_FEEDS: tuple[tuple[str, str], ...] = (
    ("SEC Litigation", "https://www.sec.gov/rss/litigation/litreleases.xml"),
    ("SEC Press", "https://www.sec.gov/news/pressreleases.rss"),
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("Google Macro", "https://news.google.com/rss/search?q=FED+rate+decision+OR+CPI+inflation&hl=en-US&gl=US&ceid=US:en"),
    ("Google Legal", "https://news.google.com/rss/search?q=SEC+lawsuit+OR+crypto+regulation&hl=en-US&gl=US&ceid=US:en"),
    ("Google Social", "https://news.google.com/rss/search?q=Elon+Musk+OR+Jerome+Powell+statement&hl=en-US&gl=US&ceid=US:en"),
)


class EventFeedProvider:
    def __init__(self, timeout: float = 10.0, transport=None) -> None:
        self._timeout = timeout
        self._transport = transport  # test seam (httpx.MockTransport)

    async def fetch_raw(self, limit: int = 40) -> list[dict]:
        items: list[dict] = []
        async with httpx.AsyncClient(
            timeout=self._timeout, headers=_HEADERS, transport=self._transport
        ) as client:
            for source, url in _FEEDS:
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                except httpx.HTTPError:
                    continue  # tolerate one bad feed; keep aggregating
                feed = feedparser.parse(resp.content)  # parse-only, no network
                for entry in feed.entries:
                    items.append(
                        {
                            "title": getattr(entry, "title", ""),
                            "summary": getattr(entry, "summary", ""),
                            "link": getattr(entry, "link", ""),
                            "published": getattr(entry, "published", ""),
                            "source": source,
                        }
                    )
                    if len(items) >= limit:
                        return items
        return items

"""RSS news provider — multi-feed tolerance and hostile-feed parsing.

News is the softest input in the system: two third-party feeds, neither of which
owes us well-formed XML. The contract pinned here is that ``fetch`` degrades
instead of raising, because a dead news feed must never take down a signal.

  * one feed failing is survivable - the other is still consulted,
  * both feeds failing yields ``[]``, not an exception,
  * missing item fields become empty strings rather than AttributeError,
  * ``limit`` is honoured across the *combined* feeds, short-circuiting the
    second fetch once enough articles exist.
"""
from __future__ import annotations

import httpx
import pytest
from tenacity import wait_none

from app.providers.rss_news_provider import RssNewsProvider


def _rss(*items: str, channel_title: str = "Yahoo Finance") -> bytes:
    body = "".join(items)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<rss version=\"2.0\"><channel><title>{channel_title}</title>{body}"
        "</channel></rss>"
    ).encode()


def _item(title="Headline", link="https://x/1", pub="Mon, 05 Jan 2026 10:00:00 GMT") -> str:
    parts = ""
    if title is not None:
        parts += f"<title>{title}</title>"
    if link is not None:
        parts += f"<link>{link}</link>"
    if pub is not None:
        parts += f"<pubDate>{pub}</pubDate>"
    return f"<item>{parts}</item>"


def _provider(handler) -> RssNewsProvider:
    return RssNewsProvider(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    monkeypatch.setattr(RssNewsProvider._get.retry, "wait", wait_none())


async def test_fetch_reads_both_feeds_and_tags_the_source():
    def handler(request):
        name = "Yahoo Finance" if "yahoo" in str(request.url) else "Google News"
        return httpx.Response(200, content=_rss(_item(title=f"{name} story"), channel_title=name))

    articles = await _provider(handler).fetch("AAPL", limit=10)
    assert [a.source for a in articles] == ["Yahoo Finance", "Google News"]
    assert articles[0].title == "Yahoo Finance story"


async def test_query_is_url_encoded_into_both_feeds():
    seen: list[str] = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=_rss(_item()))

    await _provider(handler).fetch("BRK B", limit=10)
    assert len(seen) == 2 and all("BRK+B" in u for u in seen)


async def test_limit_short_circuits_before_the_second_feed():
    """Hitting the cap on feed one must stop us fetching feed two at all."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, content=_rss(_item(), _item(), _item()))

    articles = await _provider(handler).fetch("AAPL", limit=2)
    assert len(articles) == 2
    assert calls["n"] == 1  # second feed never requested


async def test_one_dead_feed_does_not_lose_the_other():
    def handler(request):
        if "yahoo" in str(request.url):
            return httpx.Response(500, text="yahoo down")
        return httpx.Response(200, content=_rss(_item(title="survivor"), channel_title="Google News"))

    articles = await _provider(handler).fetch("AAPL", limit=10)
    assert [a.title for a in articles] == ["survivor"]


async def test_both_feeds_down_returns_empty_not_raise():
    def handler(request):
        raise httpx.ConnectError("no network")

    assert await _provider(handler).fetch("AAPL", limit=10) == []


async def test_transport_errors_are_retried_before_giving_up():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ReadTimeout("slow feed")

    assert await _provider(handler).fetch("AAPL", limit=5) == []
    assert calls["n"] == 6  # 3 attempts x 2 feeds


async def test_items_missing_fields_degrade_to_empty_strings():
    def handler(request):
        return httpx.Response(200, content=_rss(_item(title=None, link=None, pub=None)))

    articles = await _provider(handler).fetch("AAPL", limit=1)
    assert len(articles) == 1
    assert articles[0].title == "" and articles[0].published == ""


async def test_unparseable_body_yields_no_articles_but_no_crash():
    def handler(request):
        return httpx.Response(200, content=b"<<< this is not xml at all")

    assert await _provider(handler).fetch("AAPL", limit=5) == []


async def test_feed_without_channel_title_leaves_source_blank():
    def handler(request):
        return httpx.Response(
            200,
            content=b'<?xml version="1.0"?><rss version="2.0"><channel>'
            b"<item><title>No channel title</title></item></channel></rss>",
        )

    articles = await _provider(handler).fetch("AAPL", limit=1)
    assert articles[0].source == ""

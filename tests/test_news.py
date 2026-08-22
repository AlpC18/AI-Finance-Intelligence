def test_news_raw_articles_instant(client):
    res = client.get("/api/news?query=AAPL")
    assert res.status_code == 200
    body = res.json()
    assert body["query"] == "AAPL"
    assert len(body["articles"]) == 2
    assert "insight" not in body


def test_news_ai_insight_degrades(client):
    res = client.get("/api/news/ai-insights?query=AAPL")
    assert res.status_code == 200
    body = res.json()
    assert body["degraded"] is True
    assert body["sentiment"] in {"positive", "neutral", "negative"}


def test_news_requires_query(client):
    assert client.get("/api/news").status_code == 422

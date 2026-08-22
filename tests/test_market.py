def test_market_data_is_instant_and_ai_free(client):
    res = client.get("/api/market/AAPL")
    assert res.status_code == 200
    body = res.json()
    # Raw quant only — no signal key in the hot path.
    assert body["quote"]["symbol"] == "AAPL"
    assert "indicators" in body
    assert "signal" not in body


def test_market_ai_insight_degrades_safely(client):
    res = client.get("/api/market/AAPL/ai-insights")
    assert res.status_code == 200
    body = res.json()
    assert body["action"] == "HOLD"
    assert body["degraded"] is True
    assert "disclaimer" in body

def test_alerts_require_auth(client):
    assert client.get("/api/alerts").status_code == 401
    assert client.post("/api/alerts", json={}).status_code == 401


def test_alert_crud_and_isolation(client, make_user):
    alice = make_user("alice@ex.com")
    bob = make_user("bob@ex.com")

    created = client.post(
        "/api/alerts", headers=alice,
        json={"symbol": "AAPL", "condition_type": "PRICE_ABOVE", "threshold_value": 150},
    )
    assert created.status_code == 201
    alert = created.json()
    assert alert["condition_type"] == "PRICE_ABOVE"
    assert alert["is_active"] is True
    assert alert["last_triggered_at"] is None

    assert len(client.get("/api/alerts", headers=alice).json()) == 1
    assert client.get("/api/alerts", headers=bob).json() == []

    assert client.delete(f"/api/alerts/{alert['id']}", headers=bob).status_code == 404
    assert client.delete(f"/api/alerts/{alert['id']}", headers=alice).status_code == 204
    assert client.get("/api/alerts", headers=alice).json() == []

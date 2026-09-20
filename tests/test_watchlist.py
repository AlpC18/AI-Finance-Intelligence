def test_watchlist_requires_auth(client):
    assert client.get("/api/watchlist").status_code == 401


def test_watchlist_crud_duplicate_and_tenant_isolation(client, make_user):
    alice = make_user("watch-alice@ex.com")
    bob = make_user("watch-bob@ex.com")
    created = client.post(
        "/api/watchlist",
        headers=alice,
        json={"symbol": " aapl ", "note": "earnings", "target_price": "250.15"},
    )
    assert created.status_code == 201
    item = created.json()
    assert item["symbol"] == "AAPL" and item["note"] == "earnings"
    assert item["target_price"] == 250.15
    updated = client.patch(
        f"/api/watchlist/{item['id']}", headers=alice, json={"target_price": "260.25"}
    )
    assert updated.status_code == 200
    assert updated.json()["target_price"] == 260.25
    assert client.post("/api/watchlist", headers=alice, json={"symbol": "AAPL"}).status_code == 409
    assert client.get("/api/watchlist", headers=bob).json() == []
    assert client.delete(f"/api/watchlist/{item['id']}", headers=bob).status_code == 404
    assert client.delete(f"/api/watchlist/{item['id']}", headers=alice).status_code == 204


def test_watchlist_note_update_and_missing_item(client, make_user):
    headers = make_user("watch-update@ex.com")
    item = client.post("/api/watchlist", headers=headers, json={"symbol": "MSFT"}).json()
    updated = client.patch(f"/api/watchlist/{item['id']}", headers=headers, json={"note": "quality"})
    assert updated.status_code == 200
    assert updated.json()["note"] == "quality"
    assert client.patch("/api/watchlist/99999", headers=headers, json={"note": "x"}).status_code == 404

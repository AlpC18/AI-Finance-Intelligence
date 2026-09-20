from app.core.config import get_settings


def test_consumer_activity_requires_valid_key(client):
    assert client.get("/api/consumer/activity").status_code == 401


def test_consumer_key_can_read_its_own_activity(client, auth_headers):
    made = client.post("/api/developer/keys", headers=auth_headers, json={
        "name": "integration", "scopes": ["read:activity"],
    })
    assert made.status_code == 201
    response = client.get("/api/consumer/activity", headers={"X-API-Key": made.json()["secret"]})
    assert response.status_code == 200
    listed = client.get("/api/developer/keys", headers=auth_headers).json()
    assert listed[0]["request_count"] == 1


def test_admin_overview_is_fail_closed_and_allowlist_controlled(client, make_user, monkeypatch):
    headers = make_user("operator@example.com")
    assert client.get("/api/admin/overview", headers=headers).status_code == 403
    monkeypatch.setattr(get_settings(), "admin_emails", "operator@example.com", raising=False)
    response = client.get("/api/admin/overview", headers=headers)
    assert response.status_code == 200
    assert response.json()["trading_mode"] == "paper"

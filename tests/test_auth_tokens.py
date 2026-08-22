"""Refresh rotation, logout blocklist, and config preflight."""
from app.core.config import Settings


def _login(client, email="tok@ex.com"):
    client.post("/api/auth/register", json={"email": email, "password": "password123"})
    return client.post(
        "/api/auth/login", json={"email": email, "password": "password123"}
    ).json()


def test_login_returns_access_and_refresh(client):
    pair = _login(client)
    assert pair["access_token"] and pair["refresh_token"]
    assert pair["token_type"] == "bearer"


def test_refresh_issues_working_access_token(client):
    pair = _login(client, "r@ex.com")
    res = client.post("/api/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert res.status_code == 200
    new = res.json()
    headers = {"Authorization": f"Bearer {new['access_token']}"}
    assert client.get("/api/portfolio", headers=headers).status_code == 200


def test_access_token_rejected_as_refresh(client):
    pair = _login(client, "x@ex.com")
    res = client.post("/api/auth/refresh", json={"refresh_token": pair["access_token"]})
    assert res.status_code == 401


def test_rotated_refresh_token_is_revoked(client):
    pair = _login(client, "rot@ex.com")
    client.post("/api/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    # Old refresh token was rotated -> now blocklisted.
    again = client.post("/api/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert again.status_code == 401


def test_logout_blocklists_access_token(client):
    pair = _login(client, "out@ex.com")
    headers = {"Authorization": f"Bearer {pair['access_token']}"}
    assert client.get("/api/portfolio", headers=headers).status_code == 200

    logout = client.post(
        "/api/auth/logout",
        headers=headers,
        json={"refresh_token": pair["refresh_token"]},
    )
    assert logout.status_code == 204

    # Same access token must now be rejected.
    assert client.get("/api/portfolio", headers=headers).status_code == 401
    # And the logged-out refresh token cannot mint new access.
    assert client.post(
        "/api/auth/refresh", json={"refresh_token": pair["refresh_token"]}
    ).status_code == 401


def test_production_preflight_flags_placeholders():
    prod = Settings(environment="production")  # placeholder secret, no AI key
    errors = prod.production_config_errors()
    assert any("JWT_SECRET" in e for e in errors)
    assert any("ANTHROPIC_API_KEY" in e for e in errors)
    assert any("CACHE_BACKEND" in e for e in errors)


def test_production_preflight_passes_when_configured():
    from cryptography.fernet import Fernet

    prod = Settings(
        environment="production",
        jwt_secret="x" * 40,
        anthropic_api_key="sk-ant-real",
        cache_backend="redis",
        encryption_key=Fernet.generate_key().decode("utf-8"),
    )
    assert prod.production_config_errors() == []


def test_dev_never_blocks_boot():
    assert Settings(environment="development").production_config_errors() == []

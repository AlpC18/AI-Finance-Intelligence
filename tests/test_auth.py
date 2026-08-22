def test_register_and_login_flow(client):
    r = client.post("/api/auth/register", json={"email": "u@ex.com", "password": "password123"})
    assert r.status_code == 201
    assert r.json()["email"] == "u@ex.com"
    assert "hashed_password" not in r.json()

    tok = client.post("/api/auth/login", json={"email": "u@ex.com", "password": "password123"})
    assert tok.status_code == 200
    assert tok.json()["token_type"] == "bearer"
    assert tok.json()["access_token"]


def test_duplicate_email_rejected(client):
    client.post("/api/auth/register", json={"email": "dup@ex.com", "password": "password123"})
    r = client.post("/api/auth/register", json={"email": "dup@ex.com", "password": "password123"})
    assert r.status_code == 409


def test_wrong_password_401(client):
    client.post("/api/auth/register", json={"email": "w@ex.com", "password": "password123"})
    r = client.post("/api/auth/login", json={"email": "w@ex.com", "password": "wrongpass1"})
    assert r.status_code == 401


def test_short_password_rejected(client):
    r = client.post("/api/auth/register", json={"email": "s@ex.com", "password": "short"})
    assert r.status_code == 422

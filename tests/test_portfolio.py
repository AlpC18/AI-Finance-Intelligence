def _buy(client, headers, symbol, qty, price):
    return client.post(
        "/api/portfolio/transactions", headers=headers,
        json={"symbol": symbol, "action": "BUY", "quantity": qty, "price": price},
    )


def _sell(client, headers, symbol, qty, price):
    return client.post(
        "/api/portfolio/transactions", headers=headers,
        json={"symbol": symbol, "action": "SELL", "quantity": qty, "price": price},
    )


def test_portfolio_requires_auth(client):
    assert client.get("/api/portfolio").status_code == 401
    assert client.get("/api/portfolio/risk").status_code == 401
    assert _buy(client, {}, "AAPL", 1, 1).status_code == 401


def test_ledger_reconstruction_and_pnl(client, auth_headers):
    assert client.get("/api/portfolio", headers=auth_headers).json() == []

    assert _buy(client, auth_headers, "AAPL", 10, 100.0).status_code == 201
    _buy(client, auth_headers, "AAPL", 10, 120.0)          # avg -> 110
    _sell(client, auth_headers, "AAPL", 5, 130.0)          # realized = (130-110)*5 = 100

    holdings = client.get("/api/portfolio", headers=auth_headers).json()
    assert len(holdings) == 1
    h = holdings[0]
    assert h["symbol"] == "AAPL"
    assert h["quantity"] == 15
    assert h["avg_cost"] == 110.0
    assert h["realized_pnl"] == 100.0

    risk = client.get("/api/portfolio/risk", headers=auth_headers).json()
    assert risk["total_realized_pnl"] == 100.0
    p = risk["positions"][0]
    assert p["avg_cost"] == 110.0
    assert p["quantity"] == 15
    assert "unrealized_pnl" in p
    assert p["weight_pct"] == 100.0


def test_closed_position_keeps_realized_pnl(client, auth_headers):
    _buy(client, auth_headers, "MSFT", 4, 50.0)
    _sell(client, auth_headers, "MSFT", 4, 75.0)   # fully closed, realized = 100
    # No active holdings, but realized P&L persists in the report.
    assert client.get("/api/portfolio", headers=auth_headers).json() == []
    risk = client.get("/api/portfolio/risk", headers=auth_headers).json()
    assert risk["total_realized_pnl"] == 100.0
    assert risk["positions"] == []


def test_oversell_rejected(client, auth_headers):
    _buy(client, auth_headers, "AAPL", 5, 10.0)
    assert _sell(client, auth_headers, "AAPL", 10, 12.0).status_code == 400


def test_transactions_isolated(client, make_user):
    alice = make_user("alice@ex.com")
    bob = make_user("bob@ex.com")
    _buy(client, alice, "AAPL", 3, 10.0)
    assert client.get("/api/portfolio", headers=bob).json() == []
    assert len(client.get("/api/portfolio/transactions", headers=alice).json()) == 1

from datetime import datetime, timedelta, timezone

from app.core.deps import get_portfolio_service
from app.models.transaction import Transaction


def _seed(client, user_id, rows):
    session = client.app.state.test_engine
    from sqlmodel import Session
    with Session(session) as s:
        for action, qty, price, at in rows:
            s.add(Transaction(user_id=user_id, symbol="AAPL", action=action,
                              quantity=qty, price=price, timestamp=at))
        s.commit()


def test_tax_lots_support_fifo_lifo_and_export(client, auth_headers):
    now = datetime.now(timezone.utc)
    _seed(client, 1, [
        ("BUY", 2, 100, now - timedelta(days=400)),
        ("BUY", 2, 200, now - timedelta(days=10)),
        ("SELL", 3, 300, now),
    ])
    fifo = client.get(f"/api/portfolio/tax-lots?year={now.year}&method=FIFO", headers=auth_headers)
    assert fifo.status_code == 200
    assert fifo.json()["total_gain_loss"] == 500.0
    assert fifo.json()["long_term_gain_loss"] == 400.0
    lifo = client.get(f"/api/portfolio/tax-lots?year={now.year}&method=LIFO", headers=auth_headers)
    assert lifo.json()["total_gain_loss"] == 400.0
    export = client.get(f"/api/portfolio/export/tax-lots?year={now.year}", headers=auth_headers)
    assert export.status_code == 200 and "gain_loss" in export.text

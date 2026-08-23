"""Bounded list endpoints, and exports that cannot execute in a spreadsheet.

Two separate concerns that share a seam:

  pagination .. an account's history is unbounded, so an uncapped endpoint is
                a request that gets slower forever. The cap must not silently
                lie either: X-Total-Count lets a client tell a full answer from
                a truncated one without the response shape changing.
  export ...... a CSV is not inert. Any cell starting with =, +, -, @ or a
                control character is executed by Excel/Sheets on open, and the
                audit trail carries 4000 characters of free-form model text.
                So the export is tested as an injection surface, not just a
                formatting one.
"""
from __future__ import annotations

import csv
import io

import pytest

from app.core.csv_export import content_disposition, sanitize_cell, to_csv


def _record(client, headers, symbol="AAPL", action="BUY", qty=1, price=100.0):
    return client.post(
        "/api/portfolio/transactions",
        json={"symbol": symbol, "action": action, "quantity": qty, "price": price},
        headers=headers,
    )


def _rows(body: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(body)))


# ============================ CELL SANITISING ==============================

@pytest.mark.parametrize("payload", [
    "=1+1",
    "+1+1",
    "-1+1",
    "@SUM(A1:A9)",
    "=cmd|'/c calc'!A0",
    "\tSUM(1)",
    "\rSUM(1)",
])
def test_a_formula_cell_is_neutralised(payload):
    assert sanitize_cell(payload).startswith("'"), payload


def test_ordinary_text_is_left_alone():
    assert sanitize_cell("AAPL momentum + earnings beat") == "AAPL momentum + earnings beat"


def test_a_negative_number_is_not_quoted():
    """A minus sign on a number is data, not a formula — quoting corrupts it."""
    assert sanitize_cell(-42.5) == "-42.5"
    assert sanitize_cell(-7) == "-7"


def test_none_becomes_an_empty_cell_not_the_word_none():
    assert sanitize_cell(None) == ""


def test_booleans_render_readably():
    assert sanitize_cell(True) == "true" and sanitize_cell(False) == "false"


def test_commas_and_quotes_are_escaped_by_the_writer():
    body = to_csv(["a"], [['say "hi", please']])

    assert _rows(body)[1] == ['say "hi", please'], "round-trips through a CSV reader"


def test_a_newline_inside_a_cell_cannot_forge_a_row():
    body = to_csv(["a", "b"], [["line1\nline2", "x"]])

    assert len(_rows(body)) == 2, "still one header + one data row"


@pytest.mark.parametrize("hostile", [
    '../../etc/passwd"; drop',
    'a"; filename="evil.sh',
    "report\r\nX-Injected: 1",
])
def test_a_hostile_download_filename_cannot_break_the_header(hostile):
    """Only the quoted filename may vary, and it can hold no quote or newline."""
    header = content_disposition(hostile)

    assert header.startswith('attachment; filename="') and header.endswith('"')
    inner = header[len('attachment; filename="'):-1]
    assert '"' not in inner and "\r" not in inner and "\n" not in inner
    assert ";" not in inner


# ============================== PAGINATION =================================

def test_the_ledger_is_capped_by_default(client, auth_headers):
    for i in range(5):
        _record(client, auth_headers, qty=i + 1)

    res = client.get("/api/portfolio/transactions", params={"limit": 2},
                     headers=auth_headers)

    assert len(res.json()) == 2


def test_the_total_count_reports_the_full_size_of_a_truncated_page(client, auth_headers):
    """The header is what makes the cap honest rather than silent."""
    for i in range(5):
        _record(client, auth_headers, qty=i + 1)

    res = client.get("/api/portfolio/transactions", params={"limit": 2},
                     headers=auth_headers)

    assert res.headers["X-Total-Count"] == "5"
    assert len(res.json()) == 2, "the page is short, the count is not"


def test_paging_through_the_ledger_covers_every_row_once(client, auth_headers):
    for i in range(5):
        _record(client, auth_headers, qty=i + 1)

    first = client.get("/api/portfolio/transactions",
                       params={"limit": 3, "offset": 0}, headers=auth_headers).json()
    second = client.get("/api/portfolio/transactions",
                        params={"limit": 3, "offset": 3}, headers=auth_headers).json()

    ids = [t["id"] for t in first + second]
    assert len(ids) == 5 and len(set(ids)) == 5


def test_orders_are_capped_and_counted_too(client, auth_headers):
    res = client.get("/api/trade/orders", params={"limit": 1}, headers=auth_headers)

    assert res.status_code == 200
    assert res.headers["X-Total-Count"] == "0"


@pytest.mark.parametrize("path", ["/api/portfolio/transactions", "/api/trade/orders"])
@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 1001}, {"offset": -1}])
def test_an_out_of_range_page_is_rejected(client, auth_headers, path, params):
    assert client.get(path, params=params, headers=auth_headers).status_code == 422


def test_holdings_still_replay_the_whole_ledger(client, auth_headers):
    """The cap is on the HTTP read, never on the reconstruction behind it."""
    for _ in range(12):
        _record(client, auth_headers, symbol="AAPL", action="BUY", qty=1, price=10.0)

    holdings = client.get("/api/portfolio", headers=auth_headers).json()

    assert holdings[0]["quantity"] == 12, "every transaction was replayed"


# ============================== CSV EXPORT =================================

def test_the_ledger_exports_as_a_csv_attachment(client, auth_headers):
    _record(client, auth_headers, symbol="AAPL", qty=3, price=50.0)

    res = client.get("/api/portfolio/export/transactions", headers=auth_headers)

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert res.headers["content-disposition"] == 'attachment; filename="transactions.csv"'
    rows = _rows(res.text)
    assert rows[0] == ["timestamp", "symbol", "action", "quantity", "price", "value"]
    assert rows[1][1:] == ["AAPL", "BUY", "3.0", "50.0", "150.0"]


def test_the_export_ignores_the_page_cap(client, auth_headers):
    """A partial compliance export is worse than none."""
    for _ in range(12):
        _record(client, auth_headers, qty=1)

    res = client.get("/api/portfolio/export/transactions", headers=auth_headers)

    assert len(_rows(res.text)) == 13, "12 rows plus the header"


def test_an_empty_ledger_exports_a_header_only_file(client, auth_headers):
    res = client.get("/api/portfolio/export/transactions", headers=auth_headers)

    assert len(_rows(res.text)) == 1


def test_the_audit_trail_exports_as_csv(client, auth_headers):
    res = client.get("/api/trade/export/audit", headers=auth_headers)

    assert res.status_code == 200
    assert res.headers["content-disposition"] == 'attachment; filename="audit-log.csv"'
    assert _rows(res.text)[0][0] == "executed_at"


def test_a_symbol_that_looks_like_a_formula_is_neutralised_in_the_export(client, auth_headers):
    """End-to-end: hostile data recorded through the API must land inert."""
    _record(client, auth_headers, symbol="=HYPERLINK(1)", qty=1, price=1.0)

    res = client.get("/api/portfolio/export/transactions", headers=auth_headers)

    cell = _rows(res.text)[1][1]
    assert cell.startswith("'"), f"formula reached the file unescaped: {cell!r}"


@pytest.mark.parametrize("path", [
    "/api/portfolio/export/transactions", "/api/trade/export/audit",
])
def test_exports_require_authentication(client, path):
    assert client.get(path).status_code in (401, 403)


def test_one_users_export_never_contains_anothers_rows(client, make_user):
    alice = make_user("alice-export@example.com")
    bob = make_user("bob-export@example.com")
    _record(client, alice, symbol="SECRET", qty=1, price=1.0)

    res = client.get("/api/portfolio/export/transactions", headers=bob)

    assert "SECRET" not in res.text
    assert len(_rows(res.text)) == 1

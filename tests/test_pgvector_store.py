"""Persistent RAG store on Postgres/pgvector — SQL correctness without a live DB.

The store's job is to end the last per-process island: embeddings must survive a
restart and be shared by every worker. These tests drive the store through a fake
asyncpg pool so the SQL, parameter binding, vector encoding and result parsing are
all verified deterministically, and assert that ``deps`` selects it on Postgres and
degrades to the in-memory store everywhere else.
"""
from __future__ import annotations

import pytest

from app.core.pgvector_store import PgVectorStore, _normalize_dsn, _vector_literal
from app.core.vector_store import HashingEmbedder, InMemoryVectorStore, RetrievedChunk


class _FakeConn:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed: list[tuple] = []
        self.executed_many: list[tuple] = []
        self.fetched: list[tuple] = []
        self.fetchval_result = 0

    async def execute(self, sql, *args):
        self.executed.append((sql, args))

    async def executemany(self, sql, rows):
        self.executed_many.append((sql, rows))

    async def fetch(self, sql, *args):
        self.fetched.append((sql, args))
        return self.rows

    async def fetchval(self, sql, *args):
        return self.fetchval_result


class _FakePool:
    """Minimal asyncpg pool: ``async with pool.acquire() as conn``."""

    def __init__(self, conn: _FakeConn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


@pytest.fixture
def conn():
    return _FakeConn()


@pytest.fixture
def store(conn):
    return PgVectorStore("postgresql://u:p@h/db", pool=_FakePool(conn), capacity=100)


# --- Encoding helpers ---
def test_normalize_dsn_strips_sqlalchemy_driver():
    assert _normalize_dsn("postgresql+asyncpg://u:p@h/db") == "postgresql://u:p@h/db"
    assert _normalize_dsn("postgres+psycopg2://u:p@h/db") == "postgresql://u:p@h/db"
    assert _normalize_dsn("postgresql://u:p@h/db") == "postgresql://u:p@h/db"


def test_vector_literal_is_pgvector_text_format():
    assert _vector_literal([0.5, -0.25]) == "[0.500000,-0.250000]"


@pytest.mark.parametrize(
    "bad",
    [
        "rag_chunks; DROP TABLE users",
        'rag" OR 1=1 --',
        "rag chunks",
        "1rag",
        "",
        "x" * 64,
    ],
)
def test_table_name_must_be_a_safe_identifier(bad):
    """The table name is interpolated (SQL can't bind identifiers) — so it is validated."""
    with pytest.raises(ValueError, match="Unsafe SQL identifier"):
        PgVectorStore("postgresql://x", table=bad)


def test_valid_table_names_are_accepted():
    assert PgVectorStore("postgresql://x", table="rag_chunks_v2")._table == "rag_chunks_v2"


# --- Schema bootstrap ---
@pytest.mark.asyncio
async def test_schema_is_created_once_with_matching_dimension(store, conn):
    await store.add_texts([RetrievedChunk(text="hello world", link="l1")])
    await store.add_texts([RetrievedChunk(text="second one", link="l2")])

    ddl = " ".join(sql for sql, _ in conn.executed)
    assert "CREATE EXTENSION IF NOT EXISTS vector" in ddl
    assert f"vector({HashingEmbedder().dim})" in ddl
    # _ready guard: the extension is created on the first call only.
    assert ddl.count("CREATE EXTENSION") == 1


# --- Writes ---
@pytest.mark.asyncio
async def test_add_texts_inserts_dedup_keyed_rows(store, conn):
    await store.add_texts(
        [
            RetrievedChunk(text="apple earnings beat", headline="H1", source="S", link="l1"),
            RetrievedChunk(text="apple faces probe", headline="H2", source="S", link="l2"),
        ]
    )

    sql, rows = conn.executed_many[0]
    assert "ON CONFLICT (dedup_key) DO NOTHING" in sql   # dedupe across feed polls
    assert "$6::vector" in sql
    assert len(rows) == 2
    assert rows[0][0] == "l1"                             # dedup key is the link
    assert rows[0][1] == "apple earnings beat"
    assert rows[0][5].startswith("[") and rows[0][5].endswith("]")


@pytest.mark.asyncio
async def test_add_texts_trims_to_capacity(store, conn):
    await store.add_texts([RetrievedChunk(text="x", link="l1")])

    trim = [(sql, args) for sql, args in conn.executed if sql.startswith("DELETE")]
    assert trim, "capacity trim never ran"
    assert trim[0][1] == (100,)


@pytest.mark.asyncio
async def test_add_texts_skips_blank_and_empty_input(store, conn):
    await store.add_texts([])
    await store.add_texts([RetrievedChunk(text="   ", link="l1")])
    assert conn.executed_many == []
    assert conn.executed == []  # not even the schema bootstrap


# --- Reads ---
@pytest.mark.asyncio
async def test_search_orders_by_cosine_distance_and_maps_rows(conn):
    conn.rows = [
        {"text": "t1", "headline": "H1", "source": "S1", "link": "l1", "score": 0.91},
        {"text": "t2", "headline": "H2", "source": "S2", "link": "l2", "score": 0.42},
    ]
    store = PgVectorStore("postgresql://x", pool=_FakePool(conn))

    hits = await store.search("apple", k=2)

    sql, args = conn.fetched[0]
    assert "embedding <=> $1::vector" in sql  # cosine operator, indexable
    assert args[1] == 2
    assert [h.link for h in hits] == ["l1", "l2"]
    assert hits[0].score == 0.91
    assert hits[0].citation() == "H1"


@pytest.mark.asyncio
async def test_search_applies_min_score_floor(conn):
    conn.rows = [
        {"text": "t1", "headline": "", "source": "", "link": "l1", "score": 0.5},
        {"text": "t2", "headline": "", "source": "", "link": "l2", "score": 0.05},
    ]
    store = PgVectorStore("postgresql://x", pool=_FakePool(conn))

    hits = await store.search("apple", k=5, min_score=0.1)

    assert [h.link for h in hits] == ["l1"]  # weak match dropped, not returned


@pytest.mark.asyncio
async def test_search_short_circuits_on_blank_query(store, conn):
    assert await store.search("   ") == []
    assert conn.fetched == []  # no DB round trip at all


@pytest.mark.asyncio
async def test_size_counts_rows(conn):
    conn.fetchval_result = 7
    store = PgVectorStore("postgresql://x", pool=_FakePool(conn))
    assert await store.size() == 7


# --- Protocol compatibility with the in-memory store it replaces ---
@pytest.mark.asyncio
async def test_pgvector_satisfies_the_same_protocol_as_in_memory():
    for name in ("add_texts", "search", "size"):
        assert callable(getattr(PgVectorStore, name))
        assert callable(getattr(InMemoryVectorStore, name))


# --- Selection in deps: Postgres -> persistent, else in-memory ---
def test_deps_selects_pgvector_on_postgres(monkeypatch):
    from app.core import deps
    from app.core.config import Settings

    deps.get_vector_store.cache_clear()
    monkeypatch.setattr(
        deps, "get_settings", lambda: Settings(database_url="postgresql://u:p@h/db")
    )
    try:
        assert isinstance(deps.get_vector_store(), PgVectorStore)
    finally:
        deps.get_vector_store.cache_clear()


def test_deps_falls_back_to_in_memory_on_sqlite(monkeypatch):
    from app.core import deps
    from app.core.config import Settings

    deps.get_vector_store.cache_clear()
    monkeypatch.setattr(
        deps, "get_settings", lambda: Settings(database_url="sqlite:///./finance.db")
    )
    try:
        assert isinstance(deps.get_vector_store(), InMemoryVectorStore)
    finally:
        deps.get_vector_store.cache_clear()


def test_deps_degrades_to_in_memory_when_pgvector_construction_fails(monkeypatch):
    """A broken Postgres must never block boot — RAG degrades, the app serves."""
    import app.core.pgvector_store as pg
    from app.core import deps
    from app.core.config import Settings

    def _boom(*a, **kw):
        raise RuntimeError("no postgres here")

    deps.get_vector_store.cache_clear()
    monkeypatch.setattr(
        deps, "get_settings", lambda: Settings(database_url="postgresql://u:p@h/db")
    )
    monkeypatch.setattr(pg, "PgVectorStore", _boom)
    try:
        assert isinstance(deps.get_vector_store(), InMemoryVectorStore)
    finally:
        deps.get_vector_store.cache_clear()

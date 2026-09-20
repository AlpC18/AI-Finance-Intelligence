import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.models.rag import (
    DocumentSearchQuery,
    FinancialDocument,
    IngestDocumentRequest,
)
from app.services.financial_rag_service import FinancialRagService, _calc_sentiment


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_sentiment_lexicon_calculation():
    pos_text = "Strong growth with record revenue expansion and robust profitability."
    neg_text = "Severe headwind, impairment loss, and growth slowdown with uncertainty."

    assert _calc_sentiment(pos_text) > 0.3
    assert _calc_sentiment(neg_text) < -0.3


@pytest.mark.asyncio
async def test_financial_rag_ingest_and_sentiment_shift(session: Session):
    svc = FinancialRagService()

    raw_text = """
[PREPARED REMARKS]
We are thrilled to announce record revenue growth and robust margin expansion across cloud.

[Q&A SESSION]
Analyst: How are you seeing enterprise slowdown?
Executive: We face significant headwind, margin pressure, and customer cautious spending.
"""
    req = IngestDocumentRequest(
        symbol="MSFT",
        doc_type="earnings_call_transcript",
        title="Q3 2026 Earnings Call Transcript",
        raw_text=raw_text,
        fiscal_year=2026,
        fiscal_quarter=3,
    )

    doc = await svc.ingest_document(req, session)
    assert doc.id is not None
    assert doc.symbol == "MSFT"
    assert doc.sentiment_shift is not None
    # Q&A was negative while prepared was positive -> negative shift
    assert doc.sentiment_shift < 0.0

    report = svc.get_sentiment_shift_report("MSFT", session)
    assert report.symbol == "MSFT"
    assert report.sentiment_divergence < 0.0

    # Search
    q = DocumentSearchQuery(query="cloud revenue", symbol="MSFT")
    results = await svc.search(q, session)
    assert len(results) > 0

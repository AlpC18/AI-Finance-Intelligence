"""Financial Deep RAG 2.0 endpoints."""
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.api.routes.auth import get_current_user
from app.db.database import get_session
from app.models.rag import (
    DocumentSearchQuery,
    DocumentSearchResult,
    FinancialDocument,
    FinancialDocumentBase,
    IngestDocumentRequest,
    SentimentShiftReport,
)
from app.models.user import User
from app.services.financial_rag_service import FinancialRagService

router = APIRouter(prefix="/rag", tags=["financial-rag"])


@router.post("/ingest", response_model=FinancialDocumentBase)
async def ingest_financial_document(
    request: IngestDocumentRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> FinancialDocument:
    """Ingest SEC filing, earnings call transcript, or XBRL footnotes."""
    svc = FinancialRagService()
    doc = await svc.ingest_document(request, session)
    return doc


@router.post("/search", response_model=List[DocumentSearchResult])
async def search_financial_filings(
    query: DocumentSearchQuery,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> List[DocumentSearchResult]:
    """Perform semantic search across indexed filings and transcripts."""
    svc = FinancialRagService()
    results = await svc.search(query, session)
    return results


@router.get("/documents/{symbol}")
def list_symbol_documents(
    symbol: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """List ingested documents for a specific symbol."""
    docs = session.exec(
        select(FinancialDocument)
        .where(FinancialDocument.symbol == symbol.upper().strip())
        .order_by(FinancialDocument.created_at.desc())
    ).all()
    return docs


@router.get("/sentiment-shift/{symbol}", response_model=SentimentShiftReport)
def get_symbol_sentiment_shift(
    symbol: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> SentimentShiftReport:
    """Analyze executive prepared remarks vs Q&A tone divergence."""
    svc = FinancialRagService()
    return svc.get_sentiment_shift_report(symbol, session)

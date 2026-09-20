"""Financial Deep RAG 2.0 schemas.

Handles ingestion, semantic chunking, and vector search for:
- SEC 10-K / 10-Q filings
- Earnings Call Transcripts (Prepared Remarks vs Analyst Q&A)
- XBRL Financial Statement Footnotes
- Fed FOMC Minutes & Macro Releases
"""
from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field
from sqlmodel import Field as SField, SQLModel

DocumentType = Literal[
    "earnings_call_transcript",
    "sec_10k",
    "sec_10q",
    "fomc_minutes",
    "xbrl_footnote",
    "equity_research",
]


class FinancialDocumentBase(SQLModel):
    symbol: str = SField(index=True)
    doc_type: str = SField(index=True)
    title: str
    fiscal_year: Optional[int] = SField(default=None, index=True)
    fiscal_quarter: Optional[int] = SField(default=None)
    filing_date: Optional[str] = None
    source_url: Optional[str] = None
    created_at: datetime = SField(default_factory=lambda: datetime.now(timezone.utc))


class FinancialDocument(FinancialDocumentBase, table=True):
    id: Optional[int] = SField(default=None, primary_key=True)
    raw_text: str
    summary: Optional[str] = None
    sentiment_score: Optional[float] = None
    sentiment_shift: Optional[float] = None  # Q&A vs Prepared Remarks divergence


class IngestDocumentRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    doc_type: DocumentType
    title: str
    raw_text: str = Field(min_length=20)
    fiscal_year: Optional[int] = None
    fiscal_quarter: Optional[int] = None
    filing_date: Optional[str] = None
    source_url: Optional[str] = None


class DocumentChunk(BaseModel):
    chunk_id: str
    symbol: str
    section_name: str  # e.g., "MD&A", "Risk Factors", "Q&A Session", "Prepared Remarks"
    content: str
    sentiment_score: float
    is_qa_section: bool = False


class DocumentSearchQuery(BaseModel):
    query: str = Field(min_length=2)
    symbol: Optional[str] = None
    doc_type: Optional[DocumentType] = None
    section_name: Optional[str] = None
    limit: int = Field(default=5, ge=1, le=50)


class DocumentSearchResult(BaseModel):
    chunk_id: str
    symbol: str
    doc_type: str
    section_name: str
    content: str
    similarity_score: float
    sentiment_score: float
    filing_date: Optional[str] = None


class SentimentShiftReport(BaseModel):
    symbol: str
    fiscal_period: str
    prepared_remarks_sentiment: float
    qa_session_sentiment: float
    sentiment_divergence: float  # qa - remarks; negative indicates defensive management
    management_tone_summary: str
    flagged_topics: List[str] = Field(default_factory=list)

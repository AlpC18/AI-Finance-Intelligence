"""Financial Deep RAG 2.0 Service.

Provides semantic ingestion, section-aware chunking, vector indexing, and
sentiment shift detection between executive prepared remarks and analyst Q&A.
"""
import re
from typing import List, Optional
import structlog
from sqlmodel import Session, select

from app.core.vector_store import InMemoryVectorStore, VectorStore
from app.models.rag import (
    DocumentChunk,
    DocumentSearchQuery,
    DocumentSearchResult,
    FinancialDocument,
    IngestDocumentRequest,
    SentimentShiftReport,
)

logger = structlog.get_logger("financial_rag_service")

# Financial sentiment lexicons
POSITIVE_WORDS = {
    "growth", "record", "exceeded", "strong", "outperformed", "momentum",
    "profitability", "upside", "expansion", "margin", "tailwind", "bullish",
    "innovation", "accretive", "robust", "solid", "efficiency", "dividend"
}

NEGATIVE_WORDS = {
    "headwind", "decline", "missed", "slowdown", "impairment", "restructuring",
    "loss", "cautious", "pressure", "deterioration", "inflationary", "uncertainty",
    "litigation", "drawdown", "weakness", "shortfall", "risk", "softness"
}


def _calc_sentiment(text: str) -> float:
    words = re.findall(r"\b[A-Za-z]+\b", text.lower())
    if not words:
        return 0.0
    pos = sum(1 for w in words if w in POSITIVE_WORDS)
    neg = sum(1 for w in words if w in NEGATIVE_WORDS)
    total = pos + neg
    if total == 0:
        return 0.0
    return round((pos - neg) / total, 3)


class FinancialRagService:
    def __init__(self, vector_store: Optional[VectorStore] = None) -> None:
        self._vector_store = vector_store or InMemoryVectorStore()

    def chunk_document(self, text: str, symbol: str) -> List[DocumentChunk]:
        """Split financial filings into semantic sections."""
        chunks: List[DocumentChunk] = []
        raw_sections = re.split(r"(?i)\n(?:---|\bsection|\bpart\s+[0-9]|\bitem\s+[0-9]|\[|\bprepared remarks\b|\bq&a\b)", text)
        
        current_section = "Overview"
        chunk_idx = 0

        for sec in raw_sections:
            sec_clean = sec.strip()
            if not sec_clean:
                continue

            low = sec_clean.lower()
            is_qa = "q&a" in low or "question-and-answer" in low or "analyst:" in low or "operator:" in low
            if is_qa:
                current_section = "Q&A Session"
            elif "risk factor" in low:
                current_section = "Risk Factors"
            elif "management's discussion" in low or "md&a" in low:
                current_section = "MD&A"
            elif "prepared remark" in low or "executive remark" in low:
                current_section = "Prepared Remarks"
            elif "footnote" in low or "note" in low:
                current_section = "Footnotes"

            # Split large sections into ~500 character chunks
            paragraphs = [p.strip() for p in sec_clean.split("\n\n") if p.strip()]
            for p in paragraphs:
                if len(p) < 30:
                    continue
                sentiment = _calc_sentiment(p)
                chunks.append(
                    DocumentChunk(
                        chunk_id=f"{symbol}_{chunk_idx}",
                        symbol=symbol,
                        section_name=current_section,
                        content=p,
                        sentiment_score=sentiment,
                        is_qa_section=is_qa,
                    )
                )
                chunk_idx += 1

        if not chunks:
            # Fallback if no sections parsed
            sentiment = _calc_sentiment(text)
            chunks.append(
                DocumentChunk(
                    chunk_id=f"{symbol}_0",
                    symbol=symbol,
                    section_name="General",
                    content=text[:1000],
                    sentiment_score=sentiment,
                    is_qa_section=False,
                )
            )

        return chunks

    async def ingest_document(
        self, request: IngestDocumentRequest, session: Session
    ) -> FinancialDocument:
        """Ingest, analyze sentiment shift, and index into vector store."""
        sym = request.symbol.upper().strip()
        chunks = self.chunk_document(request.raw_text, sym)

        # Index chunks into vector store
        from app.core.vector_store import RetrievedChunk

        chunks_to_add = [
            RetrievedChunk(
                text=f"[{sym} | {request.doc_type} | {chunk.section_name}] {chunk.content}",
                source=f"{sym}-{request.doc_type}-{chunk.section_name}",
                headline=f"{sym} {request.doc_type} - {chunk.section_name}",
            )
            for chunk in chunks
        ]
        await self._vector_store.add_texts(chunks_to_add)

        # Calculate prepared vs QA sentiment shift
        prep_scores = [c.sentiment_score for c in chunks if not c.is_qa_section]
        qa_scores = [c.sentiment_score for c in chunks if c.is_qa_section]

        avg_prep = sum(prep_scores) / len(prep_scores) if prep_scores else 0.0
        avg_qa = sum(qa_scores) / len(qa_scores) if qa_scores else avg_prep
        sentiment_shift = round(avg_qa - avg_prep, 3)
        overall_sentiment = round((sum(prep_scores) + sum(qa_scores)) / len(chunks), 3)

        doc = FinancialDocument(
            symbol=sym,
            doc_type=request.doc_type,
            title=request.title,
            fiscal_year=request.fiscal_year,
            fiscal_quarter=request.fiscal_quarter,
            filing_date=request.filing_date,
            source_url=request.source_url,
            raw_text=request.raw_text[:20000],  # persist bounded text
            summary=f"Ingested {len(chunks)} semantic chunks. Overall sentiment: {overall_sentiment:+.2f}.",
            sentiment_score=overall_sentiment,
            sentiment_shift=sentiment_shift,
        )
        session.add(doc)
        session.commit()
        session.refresh(doc)
        logger.info(
            "financial_document_ingested",
            symbol=sym,
            doc_type=request.doc_type,
            chunks=len(chunks),
            sentiment_shift=sentiment_shift,
        )
        return doc

    async def search(
        self, query: DocumentSearchQuery, session: Session
    ) -> List[DocumentSearchResult]:
        """Perform semantic search with metadata filters."""
        raw_results = await self._vector_store.search(query.query, k=query.limit)
        results: List[DocumentSearchResult] = []

        for idx, res in enumerate(raw_results):
            text = res.text
            # Extract tags if formatted as [SYM | DOCTYPE | SECTION]
            sym = query.symbol or "UNKNOWN"
            doc_type = query.doc_type or "sec_filing"
            section = "General"

            match = re.match(r"\[(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\]\s*(.*)", text, re.DOTALL)
            if match:
                sym = match.group(1).strip()
                doc_type = match.group(2).strip()
                section = match.group(3).strip()
                body = match.group(4).strip()
            else:
                body = text

            if query.symbol and sym.upper() != query.symbol.upper():
                continue
            if query.doc_type and doc_type != query.doc_type:
                continue

            results.append(
                DocumentSearchResult(
                    chunk_id=f"res_{idx}",
                    symbol=sym,
                    doc_type=doc_type,
                    section_name=section,
                    content=body,
                    similarity_score=round(res.score, 3),
                    sentiment_score=_calc_sentiment(body),
                )
            )

        return results

    def get_sentiment_shift_report(
        self, symbol: str, session: Session
    ) -> SentimentShiftReport:
        """Analyze recent filings/transcripts for management tone divergence."""
        sym = symbol.upper().strip()
        docs = session.exec(
            select(FinancialDocument)
            .where(FinancialDocument.symbol == sym)
            .order_by(FinancialDocument.created_at.desc())
        ).all()

        if not docs:
            return SentimentShiftReport(
                symbol=sym,
                fiscal_period="N/A",
                prepared_remarks_sentiment=0.0,
                qa_session_sentiment=0.0,
                sentiment_divergence=0.0,
                management_tone_summary="No financial transcripts or SEC filings ingested yet for this symbol.",
                flagged_topics=[],
            )

        doc = docs[0]
        shift = doc.sentiment_shift or 0.0
        prep = doc.sentiment_score or 0.0
        qa = prep + shift

        flagged = []
        if shift < -0.20:
            flagged.append("Defensive tone detected in Q&A compared to prepared script")
            flagged.append("Management evasiveness or margin pressure concerns")
        elif shift > +0.20:
            flagged.append("Confident tone expansion during unfiltered analyst questioning")

        period = f"FY{doc.fiscal_year or '2024'} Q{doc.fiscal_quarter or 'Q'}"
        summary = (
            f"Sentiment Shift Divergence: {shift:+.2f}. "
            + ("Management showed caution during analyst cross-examination." if shift < -0.15 else "Management maintained consistent confidence across prepared remarks and Q&A.")
        )

        return SentimentShiftReport(
            symbol=sym,
            fiscal_period=period,
            prepared_remarks_sentiment=prep,
            qa_session_sentiment=qa,
            sentiment_divergence=shift,
            management_tone_summary=summary,
            flagged_topics=flagged,
        )

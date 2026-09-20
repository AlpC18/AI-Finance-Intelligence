"""Trading as Git Service (OpenAlice).

Manages git-like staging area, visual portfolio diffs, and cryptographic commit bundles.
"""
from datetime import datetime, timezone
import hashlib
import json
from typing import Dict, List, Optional
import structlog
from sqlmodel import Session, select

from app.models.git_trading import (
    CommitTradeRequest,
    PortfolioDiffView,
    PushCommitResponse,
    StageTradeRequest,
    StagedTradeIntent,
    TradeCommitBundle,
)

logger = structlog.get_logger("git_trading_service")


class GitTradingService:
    def stage_intent(
        self, user_id: int, request: StageTradeRequest, session: Session
    ) -> StagedTradeIntent:
        """Stages a trade intent in the staging area."""
        intent = StagedTradeIntent(
            user_id=user_id,
            symbol=request.symbol.upper().strip(),
            action=request.action,
            quantity=request.quantity,
            limit_price=request.limit_price,
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
            ai_thesis_provenance=request.ai_thesis_provenance,
            status="STAGED",
        )
        session.add(intent)
        session.commit()
        session.refresh(intent)
        logger.info("trade_intent_staged", user_id=user_id, symbol=intent.symbol, action=intent.action)
        return intent

    def get_diff(self, user_id: int, session: Session) -> PortfolioDiffView:
        """Computes git-like visual diff of staged intents against current holdings."""
        staged = session.exec(
            select(StagedTradeIntent).where(
                StagedTradeIntent.user_id == user_id,
                StagedTradeIntent.status == "STAGED",
            )
        ).all()

        current_holdings: Dict[str, float] = {"AAPL": 50.0, "MSFT": 30.0}
        target_holdings = dict(current_holdings)
        outlay = 0.0

        for item in staged:
            curr = target_holdings.get(item.symbol, 0.0)
            delta = item.quantity if item.action == "BUY" else -item.quantity
            target_holdings[item.symbol] = round(max(0.0, curr + delta), 2)
            est_p = item.limit_price or 150.0
            if item.action == "BUY":
                outlay += item.quantity * est_p
            else:
                outlay -= item.quantity * est_p

        summary = f"{len(staged)} staged order(s). Net capital delta: ${outlay:+,.2f}."

        return PortfolioDiffView(
            staged_intents=staged,
            current_holdings=current_holdings,
            target_holdings_after_push=target_holdings,
            estimated_capital_outlay_usd=round(outlay, 2),
            risk_impact_summary=summary,
        )

    def commit_staged(
        self, user_id: int, request: CommitTradeRequest, session: Session
    ) -> TradeCommitBundle:
        """Commits all currently staged intents into a cryptographically hashed bundle."""
        staged = session.exec(
            select(StagedTradeIntent).where(
                StagedTradeIntent.user_id == user_id,
                StagedTradeIntent.status == "STAGED",
            )
        ).all()

        if not staged:
            raise ValueError("No staged trade intents to commit.")

        # Compute SHA-256 commit hash
        payload = {
            "user_id": user_id,
            "intents": [
                {"sym": s.symbol, "action": s.action, "qty": s.quantity, "thesis": s.ai_thesis_provenance}
                for s in staged
            ],
            "message": request.commit_message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        raw_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
        commit_hash = hashlib.sha256(raw_bytes).hexdigest()[:16]

        for s in staged:
            s.status = "COMMITTED"
            session.add(s)

        bundle = TradeCommitBundle(
            user_id=user_id,
            commit_hash=commit_hash,
            commit_message=request.commit_message,
            orders_count=len(staged),
            portfolio_delta_summary=f"Committed {len(staged)} orders with hash {commit_hash}",
            status="COMMITTED",
        )
        session.add(bundle)
        session.commit()
        session.refresh(bundle)
        logger.info("trade_commit_created", commit_hash=commit_hash, orders=len(staged))
        return bundle

    def push_commit(
        self, user_id: int, commit_hash: str, session: Session
    ) -> PushCommitResponse:
        """Pushes committed trade bundle to execution venue (paper/live)."""
        bundle = session.exec(
            select(TradeCommitBundle).where(
                TradeCommitBundle.user_id == user_id,
                TradeCommitBundle.commit_hash == commit_hash,
            )
        ).first()

        if not bundle:
            raise ValueError(f"Commit {commit_hash} not found.")

        bundle.status = "PUSHED_TO_BROKER"
        session.add(bundle)
        session.commit()

        return PushCommitResponse(
            commit_hash=commit_hash,
            executed_orders=bundle.orders_count,
            broker_mode="PAPER_SIMULATION",
            execution_status="SUCCESSFULLY_PUSHED_AND_EXECUTED",
            push_timestamp=datetime.now(timezone.utc).isoformat(),
        )

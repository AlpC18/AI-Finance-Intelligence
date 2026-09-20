"""Advanced Quant & Agentic Trading Frameworks endpoints.

Unifies signatures from top GitHub AI trading frameworks:
- virattt/ai-hedge-fund & xbtlin/ai-berkshire: Deterministic DCF & Munger Inversion
- TauricResearch/TradingAgents: Adversarial Red-Team / Blue-Team Audit
- AI4Finance/FinRL-X: Mathematical Contract-Preserving Weight Abstraction w_t
- TraderAlice/OpenAlice: Trading as Git (Stage, Diff, Commit, Push)
- Freqtrade/FreqAI & nofx: Adaptive ML Alpha & Hardcoded Execution Limit Guard
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.api.routes.auth import get_current_user
from app.db.database import get_session
from app.models.adaptive_ml import HardcodedRuntimeGuardStatus, MLAlphaSignal
from app.models.adversarial import AdversarialAuditReport
from app.models.finrl_contract import FinRLWeightPipelineResult
from app.models.git_trading import (
    CommitTradeRequest,
    PortfolioDiffView,
    PushCommitResponse,
    StageTradeRequest,
    StagedTradeIntent,
    TradeCommitBundle,
)
from app.models.user import User
from app.models.valuation import CompleteValuationReport
from app.services.adaptive_ml_service import AdaptiveMLService
from app.services.adversarial_audit_service import AdversarialAuditService
from app.services.finrl_engine import FinRLWeightEngine
from app.services.git_trading_service import GitTradingService
from app.services.valuation_service import ValuationService

router = APIRouter(prefix="/quant-advanced", tags=["advanced-quant-frameworks"])


# 1. Deterministic Valuation & Munger Inversion (ai-berkshire & FinRobot)
@router.get("/valuation/{symbol}", response_model=CompleteValuationReport)
def get_valuation_report(
    symbol: str,
    current_price: float = Query(default=150.0, gt=0),
    current_user: User = Depends(get_current_user),
) -> CompleteValuationReport:
    """Generate 3-scenario DCF, Reverse DCF, and Charlie Munger Inversion analysis."""
    svc = ValuationService()
    return svc.generate_full_report(symbol=symbol, current_price=current_price)


# 2. Adversarial Red-Team / Blue-Team Audit (TradingAgents)
@router.post("/adversarial/audit", response_model=AdversarialAuditReport)
def run_adversarial_audit(
    symbol: str,
    action: str = "BUY",
    price: float = 150.0,
    rsi: float = 48.0,
    pe_ratio: float = 24.0,
    debt_to_equity: float = 1.2,
    news_sentiment: float = 0.1,
    current_user: User = Depends(get_current_user),
) -> AdversarialAuditReport:
    """Subject trade proposal to aggressive Red-Team audit before execution."""
    svc = AdversarialAuditService()
    return svc.audit_trade_proposal(
        symbol=symbol,
        action=action,
        price=price,
        rsi=rsi,
        pe_ratio=pe_ratio,
        debt_to_equity=debt_to_equity,
        recent_news_sentiment=news_sentiment,
    )


# 3. Mathematical Contract Weight Pipeline (FinRL-X)
@router.post("/finrl/weights", response_model=FinRLWeightPipelineResult)
def calculate_finrl_weight_pipeline(
    vix: float = Query(default=16.5, ge=5.0, le=90.0),
    method: str = Query(default="risk_parity"),
    current_user: User = Depends(get_current_user),
) -> FinRLWeightPipelineResult:
    """Enforce mathematical portfolio weight abstraction: w_t = R_t(T_t(A_t(S_t(X_<=t))))."""
    svc = FinRLWeightEngine()
    candidates = [
        {"symbol": "AAPL", "volatility": 0.22, "volume": 50_000_000, "is_halted": False},
        {"symbol": "MSFT", "volatility": 0.20, "volume": 25_000_000, "is_halted": False},
        {"symbol": "NVDA", "volatility": 0.42, "volume": 70_000_000, "is_halted": False},
        {"symbol": "AMZN", "volatility": 0.26, "volume": 35_000_000, "is_halted": False},
    ]
    return svc.process_weights(
        candidate_assets=candidates,
        volatility_index_vix=vix,
        target_allocation_method=method,
    )


# 4. Trading as Git (OpenAlice)
@router.post("/git/stage", response_model=StagedTradeIntent)
def stage_trade_intent(
    request: StageTradeRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> StagedTradeIntent:
    """Stage a trade intent with thesis provenance in the Git workspace."""
    svc = GitTradingService()
    return svc.stage_intent(current_user.id, request, session)


@router.get("/git/diff", response_model=PortfolioDiffView)
def get_staged_trade_diff(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> PortfolioDiffView:
    """View visual diff between current holdings and staged trade intentions."""
    svc = GitTradingService()
    return svc.get_diff(current_user.id, session)


@router.post("/git/commit", response_model=TradeCommitBundle)
def commit_staged_trades(
    request: CommitTradeRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> TradeCommitBundle:
    """Bundle staged trades into a cryptographically hashed commit."""
    svc = GitTradingService()
    try:
        return svc.commit_staged(current_user.id, request, session)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/git/push/{commit_hash}", response_model=PushCommitResponse)
def push_trade_commit(
    commit_hash: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> PushCommitResponse:
    """Push committed trade bundle to execution venue."""
    svc = GitTradingService()
    try:
        return svc.push_commit(current_user.id, commit_hash, session)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# 5. Adaptive ML & Hardcoded Limit Guard (FreqAI & nofx)
@router.get("/ml/alpha/{symbol}", response_model=MLAlphaSignal)
def get_adaptive_ml_alpha(
    symbol: str,
    price: float = 150.0,
    rsi: float = 50.0,
    macd_hist: float = 0.2,
    current_user: User = Depends(get_current_user),
) -> MLAlphaSignal:
    """Extract adaptive ML feature scores and regime classification."""
    svc = AdaptiveMLService()
    return svc.compute_alpha_signal(symbol=symbol, price=price, rsi=rsi, macd_hist=macd_hist)


@router.get("/runtime-guard/status", response_model=HardcodedRuntimeGuardStatus)
def get_runtime_guard_status(
    failures: int = 0,
    daily_drawdown: float = 0.5,
    current_user: User = Depends(get_current_user),
) -> HardcodedRuntimeGuardStatus:
    """Check hardcoded execution limit runtime and observation-only fail-safe."""
    svc = AdaptiveMLService()
    return svc.evaluate_runtime_guard(consecutive_failures=failures, daily_drawdown_pct=daily_drawdown)


# 6. Named Legendary Investor Boardroom (ai-hedge-fund & ai-berkshire)
from app.models.boardroom import BoardroomDebateSummary
from app.models.market_impact import AlmgrenChrissImpactResult
from app.models.pit_mtf import MultiTimeframeConfluenceReport
from app.services.boardroom_service import BoardroomService
from app.services.dossier_generator_service import DossierGeneratorService
from app.services.market_impact_service import MarketImpactService
from app.services.pit_mtf_service import PointInTimeMtfService
from fastapi.responses import PlainTextResponse


@router.get("/boardroom/debate/{symbol}", response_model=BoardroomDebateSummary)
def get_boardroom_debate(
    symbol: str,
    price: float = 150.0,
    pe_ratio: float = 24.0,
    debt_to_equity: float = 1.1,
    fcf_yield: float = 4.5,
    growth_3y: float = 14.0,
    current_user: User = Depends(get_current_user),
) -> BoardroomDebateSummary:
    """Run simulated boardroom committee among Buffett, Munger, Cathie Wood, Burry, Ackman, Dalio."""
    svc = BoardroomService()
    return svc.convene_boardroom(
        symbol=symbol,
        price=price,
        pe_ratio=pe_ratio,
        debt_to_equity=debt_to_equity,
        fcf_yield_pct=fcf_yield,
        revenue_growth_3y_pct=growth_3y,
    )


# 7. Multi-Timeframe (MTF) Confluence Engine (Jesse & FinRL)
@router.get("/mtf-confluence/{symbol}", response_model=MultiTimeframeConfluenceReport)
def get_mtf_confluence(
    symbol: str,
    price: float = 150.0,
    current_user: User = Depends(get_current_user),
) -> MultiTimeframeConfluenceReport:
    """Evaluate 1D, 1H, 15M, and 5M trend and trigger confluence."""
    svc = PointInTimeMtfService()
    return svc.analyze_multi_timeframe_confluence(symbol=symbol, price=price)


# 8. Almgren-Chriss Institutional Slippage (Nautilus Trader)
@router.get("/market-impact/slippage/{symbol}", response_model=AlmgrenChrissImpactResult)
def calculate_market_impact(
    symbol: str,
    shares: float = 10_000.0,
    price: float = 150.0,
    current_user: User = Depends(get_current_user),
) -> AlmgrenChrissImpactResult:
    """Calculate non-linear square-root law market impact and optimal execution algo."""
    svc = MarketImpactService()
    return svc.calculate_slippage(symbol=symbol, order_shares=shares, market_price=price)


# 9. 1-Click Markdown Trade Dossier Generator (FinRobot & OpenAlice)
@router.get("/dossier/{symbol}", response_class=PlainTextResponse)
def get_markdown_dossier(
    symbol: str,
    price: float = 150.0,
    current_user: User = Depends(get_current_user),
) -> str:
    """Generate institutional Markdown trade dossier for symbol."""
    svc = DossierGeneratorService()
    return svc.generate_markdown_dossier(symbol=symbol, current_price=price)


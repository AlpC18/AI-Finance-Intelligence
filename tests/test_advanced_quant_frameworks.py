import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.models.git_trading import CommitTradeRequest, StageTradeRequest
from app.models.user import User
from app.services.adaptive_ml_service import AdaptiveMLService
from app.services.adversarial_audit_service import AdversarialAuditService
from app.services.finrl_engine import FinRLWeightEngine
from app.services.git_trading_service import GitTradingService
from app.services.valuation_service import ValuationService


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        user = User(id=1, email="lead_trader@desk.com", hashed_password="pw")
        s.add(user)
        s.commit()
        yield s


def test_valuation_service_dcf_and_munger_inversion():
    svc = ValuationService()
    report = svc.generate_full_report(
        symbol="AAPL",
        current_price=150.0,
        shares_outstanding_m=1000.0,
        total_debt_m=10000.0,
        cash_m=20000.0,
        base_fcf_m=10000.0,
        historical_fcf_growth_pct=15.0,
        debt_to_equity=0.8,
        interest_coverage=8.0,
        share_dilution_pct_annual=0.2,
        pe_ratio=22.0,
    )

    assert report.symbol == "AAPL"
    assert "base" in report.scenarios
    assert report.scenarios["base"].fair_value_per_share > 0
    assert report.reverse_dcf.implied_5y_fcf_growth_rate_pct is not None
    assert report.munger_inversion.quick_kill.passed is True
    assert len(report.munger_inversion.mirror_test_5_sentences) == 5


def test_adversarial_red_team_audit():
    svc = AdversarialAuditService()

    # Highly overvalued stock with excessive debt
    report = svc.audit_trade_proposal(
        symbol="MEME_CORP",
        action="BUY",
        price=100.0,
        rsi=82.0,
        pe_ratio=65.0,
        debt_to_equity=3.8,
        recent_news_sentiment=-0.2,
    )

    assert report.symbol == "MEME_CORP"
    assert report.proposal_survived is False
    assert report.audit_verdict == "REJECTED_RED_TEAM_KILL"
    assert report.red_team.severity_score > 60.0
    assert "Red Team" in report.red_team.opposing_team


def test_finrl_weight_abstraction_contract():
    svc = FinRLWeightEngine()
    candidates = [
        {"symbol": "AAPL", "volatility": 0.20, "volume": 50_000_000, "is_halted": False},
        {"symbol": "MSFT", "volatility": 0.18, "volume": 30_000_000, "is_halted": False},
        {"symbol": "HALTED_STOCK", "volatility": 0.50, "volume": 10_000_000, "is_halted": True},
    ]

    res = svc.process_weights(
        candidate_assets=candidates,
        volatility_index_vix=20.0,
        max_single_weight=0.25,
        target_allocation_method="risk_parity",
    )

    # HALTED_STOCK must be rejected by S_t
    assert "HALTED_STOCK" in res.selection_s_t.rejected_assets
    assert "AAPL" in res.final_portfolio_weights
    assert "MSFT" in res.final_portfolio_weights
    # Sum of weights + cash = 1.0
    total_weights = sum(res.final_portfolio_weights.values())
    assert total_weights <= 1.0
    assert res.risk_overlay_r_t.cash_buffer_pct >= 0


def test_trading_as_git_lifecycle(session: Session):
    svc = GitTradingService()

    # 1. Stage intent
    req = StageTradeRequest(
        symbol="NVDA",
        action="BUY",
        quantity=10.0,
        limit_price=125.0,
        stop_loss=118.0,
        take_profit=140.0,
        ai_thesis_provenance="Breakout confirmation with verified fundamental backing",
    )
    staged = svc.stage_intent(user_id=1, request=req, session=session)
    assert staged.id is not None
    assert staged.status == "STAGED"

    # 2. View Diff
    diff = svc.get_diff(user_id=1, session=session)
    assert len(diff.staged_intents) == 1
    assert diff.target_holdings_after_push.get("NVDA") == 10.0

    # 3. Commit
    commit = svc.commit_staged(
        user_id=1,
        request=CommitTradeRequest(commit_message="feat(trade): buy 10 NVDA breakout"),
        session=session,
    )
    assert commit.commit_hash is not None
    assert commit.orders_count == 1
    assert commit.status == "COMMITTED"

    # 4. Push
    push_res = svc.push_commit(user_id=1, commit_hash=commit.commit_hash, session=session)
    assert push_res.executed_orders == 1
    assert push_res.execution_status == "SUCCESSFULLY_PUSHED_AND_EXECUTED"


def test_adaptive_ml_and_runtime_guard():
    svc = AdaptiveMLService()

    # ML Alpha Signal
    sig = svc.compute_alpha_signal("AAPL", price=150.0, rsi=32.0, macd_hist=0.4)
    assert sig.symbol == "AAPL"
    assert -1.0 <= sig.predicted_alpha_score <= 1.0
    assert sig.market_regime in {"TRENDING_BULL", "TRENDING_BEAR", "CHOPPY_RANGING", "HIGH_VOLATILITY_PANIC"}

    # Hardcoded Runtime Guard Tripped (3 failures)
    guard = svc.evaluate_runtime_guard(consecutive_failures=3, daily_drawdown_pct=1.0)
    assert guard.guard_tripped is True
    assert guard.runtime_mode == "OBSERVATION_ONLY_LOCKED"
    assert guard.human_override_required is True

    # Healthy runtime
    healthy_guard = svc.evaluate_runtime_guard(consecutive_failures=0, daily_drawdown_pct=0.8)
    assert healthy_guard.guard_tripped is False
    assert healthy_guard.runtime_mode == "ACTIVE_EXECUTION"

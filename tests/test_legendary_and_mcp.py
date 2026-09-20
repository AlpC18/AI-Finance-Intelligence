import pytest
from app.models.pit_mtf import PITSanitizerResult
from app.services.boardroom_service import BoardroomService
from app.services.dossier_generator_service import DossierGeneratorService
from app.services.market_impact_service import MarketImpactService
from app.services.pit_mtf_service import PointInTimeMtfService
from fastapi.testclient import TestClient
from app.main import create_app


def test_boardroom_debate_personas():
    svc = BoardroomService()

    # High-quality compounder: Low P/E, Low D/E, High ROIC, High Growth
    summary = svc.convene_boardroom(
        symbol="AAPL",
        price=180.0,
        pe_ratio=22.0,
        debt_to_equity=0.8,
        fcf_yield_pct=5.2,
        revenue_growth_3y_pct=26.0,
        roic_pct=24.0,
    )

    assert summary.symbol == "AAPL"
    assert "Warren Buffett" in summary.investor_opinions
    assert "Charlie Munger" in summary.investor_opinions
    assert "Cathie Wood" in summary.investor_opinions
    assert "Michael Burry" in summary.investor_opinions
    assert summary.boardroom_consensus_vote in {"STRONG_BUY", "BUY"}
    assert summary.recommended_portfolio_weight_pct > 0.0


def test_point_in_time_sanitizer_and_mtf():
    svc = PointInTimeMtfService()

    # 1. PIT Sanitizer
    filings = [
        {"period_ended": "2024-03-31", "filing_date_published": "2024-04-15T00:00:00Z", "revenue_m": 5000.0},
        {"period_ended": "2024-06-30", "filing_date_published": "2024-07-20T00:00:00Z", "revenue_m": 5500.0},
        {"period_ended": "2024-09-30", "filing_date_published": "2024-10-25T00:00:00Z", "revenue_m": 6000.0},
    ]

    # Evaluate at May 2024 (should only allow Q1, pruning Q2 and Q3)
    pit_res = svc.sanitize_point_in_time("NVDA", "2024-05-01T00:00:00Z", filings)
    assert len(pit_res.eligible_historical_filings) == 1
    assert pit_res.leaked_future_filings_pruned_count == 2
    assert pit_res.lookahead_bias_detected is True

    # 2. MTF Confluence
    mtf_res = svc.analyze_multi_timeframe_confluence("NVDA", 120.0, daily_rsi=58.0, hourly_rsi=52.0, m15_rsi=35.0)
    assert mtf_res.confluence_status == "STRONG_ALIGNMENT_LONG"
    assert mtf_res.tradeable is True


def test_almgren_chriss_market_impact():
    svc = MarketImpactService()

    # 5,000 shares on 10M ADV stock
    res_small = svc.calculate_slippage("AAPL", 5_000, 150.0, adv_shares=10_000_000)
    assert res_small.expected_slippage_bps < 15.0
    assert res_small.liquidity_regime == "HIGH_LIQUIDITY_NEGLIGIBLE_IMPACT"

    # 2,000,000 shares on 5M ADV illiquid stock
    res_large = svc.calculate_slippage("ILLIQ", 2_000_000, 50.0, adv_shares=5_000_000)
    assert res_large.expected_slippage_bps > 35.0
    assert res_large.recommended_execution_algorithm in {"VWAP_ALL_DAY", "ICEBERG_DISPATCH"}


def test_markdown_trade_dossier_generation():
    svc = DossierGeneratorService()
    dossier = svc.generate_markdown_dossier("MSFT", current_price=420.0)

    assert "# INSTITUTIONAL TRADE DOSSIER: MSFT" in dossier
    assert "Warren Buffett" in dossier
    assert "Charlie Munger 5-Sentence Mirror Test" in dossier
    assert "SHA256:" in dossier
    assert "Adversarial Red-Team / Blue-Team Audit" in dossier


def test_mcp_financial_server_rpc():
    app = create_app()
    client = TestClient(app)

    # 1. MCP Initialize
    init_res = client.post("/api/mcp/rpc", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init_res.status_code == 200
    assert init_res.json()["result"]["serverInfo"]["name"] == "ai-finance-intelligence-mcp"

    # 2. MCP Tools List
    list_res = client.post("/api/mcp/rpc", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert list_res.status_code == 200
    tools = list_res.json()["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    assert "get_valuation_dcf" in tool_names
    assert "convene_boardroom_debate" in tool_names
    assert "simulate_paper_order" in tool_names

    # 3. MCP Tool Call (get_valuation_dcf)
    call_res = client.post(
        "/api/mcp/rpc",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "get_valuation_dcf", "arguments": {"symbol": "AAPL", "current_price": 150.0}},
        },
    )
    assert call_res.status_code == 200
    assert len(call_res.json()["result"]["content"]) > 0

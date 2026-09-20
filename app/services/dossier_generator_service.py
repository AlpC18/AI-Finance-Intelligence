"""1-Click Markdown Trade Dossier Generator Service (FinRobot & OpenAlice).

Compiles institutional-grade research dossiers into standardized Markdown.
"""
from datetime import datetime, timezone
import hashlib
from typing import Optional
import structlog

from app.services.adversarial_audit_service import AdversarialAuditService
from app.services.boardroom_service import BoardroomService
from app.services.market_impact_service import MarketImpactService
from app.services.pit_mtf_service import PointInTimeMtfService
from app.services.valuation_service import ValuationService

logger = structlog.get_logger("dossier_generator")


class DossierGeneratorService:
    def generate_markdown_dossier(
        self,
        symbol: str,
        current_price: float = 150.0,
        pe_ratio: float = 24.0,
        debt_to_equity: float = 1.1,
        fcf_yield_pct: float = 4.8,
        revenue_growth_pct: float = 14.5,
    ) -> str:
        """Generates comprehensive Markdown investment dossier."""
        sym = symbol.upper().strip()
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        # 1. Valuation
        val_svc = ValuationService()
        val_report = val_svc.generate_full_report(sym, current_price, pe_ratio=pe_ratio, debt_to_equity=debt_to_equity)

        # 2. Boardroom
        board_svc = BoardroomService()
        board_report = board_svc.convene_boardroom(sym, current_price, pe_ratio, debt_to_equity, fcf_yield_pct, revenue_growth_pct)

        # 3. Adversarial Red Team
        adv_svc = AdversarialAuditService()
        adv_report = adv_svc.audit_trade_proposal(sym, "BUY", current_price, 48.0, pe_ratio, debt_to_equity)

        # 4. Multi-Timeframe Confluence
        mtf_svc = PointInTimeMtfService()
        mtf_report = mtf_svc.analyze_multi_timeframe_confluence(sym, current_price)

        # 5. Market Impact
        impact_svc = MarketImpactService()
        impact_report = impact_svc.calculate_slippage(sym, 10_000, current_price)

        # Compute cryptographic audit stamp
        raw_doc_str = f"{sym}|{current_price}|{val_report.final_valuation_verdict}|{timestamp}"
        audit_hash = hashlib.sha256(raw_doc_str.encode("utf-8")).hexdigest()[:16]

        md = f"""# INSTITUTIONAL TRADE DOSSIER: {sym}
**Generated At:** `{timestamp}` | **Audit Signature:** `SHA256:{audit_hash}` | **Current Price:** `${current_price:.2f}`

---

## 1. Executive Summary & Boardroom Committee
- **Boardroom Consensus Vote:** `{board_report.boardroom_consensus_vote}` (Alignment: {board_report.boardroom_alignment_score:.1f}%)
- **Recommended Allocation:** `{board_report.recommended_portfolio_weight_pct:.1f}% of Equity`
- **Synthesis:** {board_report.synthesized_thesis}

### Master Investor Scorecard
| Investor | Philosophy | Vote | Conviction | Key Thesis |
| :--- | :--- | :--- | :--- | :--- |
| **Warren Buffett** | Quality Moat & Sensible Price | `{board_report.investor_opinions['Warren Buffett'].vote}` | {board_report.investor_opinions['Warren Buffett'].conviction_score_100:.0f}% | {board_report.investor_opinions['Warren Buffett'].primary_thesis} |
| **Charlie Munger** | Inversion & Anti-Fragility | `{board_report.investor_opinions['Charlie Munger'].vote}` | {board_report.investor_opinions['Charlie Munger'].conviction_score_100:.0f}% | {board_report.investor_opinions['Charlie Munger'].primary_thesis} |
| **Cathie Wood** | Exponential S-Curve Innovation | `{board_report.investor_opinions['Cathie Wood'].vote}` | {board_report.investor_opinions['Cathie Wood'].conviction_score_100:.0f}% | {board_report.investor_opinions['Cathie Wood'].primary_thesis} |
| **Michael Burry** | Forensic Accounting & Deep Value | `{board_report.investor_opinions['Michael Burry'].vote}` | {board_report.investor_opinions['Michael Burry'].conviction_score_100:.0f}% | {board_report.investor_opinions['Michael Burry'].primary_thesis} |

---

## 2. Deterministic Valuation (Three-Scenario DCF & Reverse DCF)
- **Base Case Fair Value:** `${val_report.scenarios['base'].fair_value_per_share:.2f}` ({val_report.margin_of_safety_pct:+.1f}% Margin of Safety)
- **Bear Case Fair Value:** `${val_report.scenarios['bear'].fair_value_per_share:.2f}`
- **Bull Case Fair Value:** `${val_report.scenarios['bull'].fair_value_per_share:.2f}`
- **Reverse DCF Implied Growth:** `{val_report.reverse_dcf.implied_5y_fcf_growth_rate_pct:.1f}%/yr` ({val_report.reverse_dcf.market_expectation_assessment.replace('_', ' ')})

### Charlie Munger 5-Sentence Mirror Test
{chr(10).join(val_report.munger_inversion.mirror_test_5_sentences)}

---

## 3. Adversarial Red-Team / Blue-Team Audit
- **Proposal Survival Verdict:** `{adv_report.audit_verdict}` (Survival Score: `{adv_report.survival_score_100:.1f}/100`)
- **Blue Team Argument:** {adv_report.blue_team.growth_thesis}
- **Red Team Counter-Thesis:** {adv_report.red_team.bear_counter_thesis}

---

## 4. Multi-Timeframe Confluence & Market Impact
- **MTF Trend Confluence:** `{mtf_report.confluence_status}` ({mtf_report.confluence_score_pct:.0f}% Alignment)
- **10,000 Share Order Slippage:** `{impact_report.expected_slippage_bps:.1f} bps` (${impact_report.expected_slippage_dollars:.2f}/share)
- **Recommended Execution Algo:** `{impact_report.recommended_execution_algorithm}`
"""
        return md.strip()

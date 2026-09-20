"""FinRL-X Contract-Preserving Weight Abstraction Service.

Computes portfolio allocation through the 4-layer mathematical pipeline:
    w_t = R_t( T_t( A_t( S_t( X_<=t ) ) ) )
"""
from datetime import datetime, timezone
from typing import Dict, List, Optional
import structlog

from app.models.finrl_contract import (
    AssetSelectionOutput,
    FinRLWeightPipelineResult,
    RawAllocationOutput,
    RiskOverlayOutput,
    TimingAdjustmentOutput,
)

logger = structlog.get_logger("finrl_engine")


class FinRLWeightEngine:
    def process_weights(
        self,
        candidate_assets: List[dict],
        volatility_index_vix: float = 16.5,
        max_single_weight: float = 0.15,
        target_allocation_method: str = "risk_parity",
    ) -> FinRLWeightPipelineResult:
        """Executes w_t = R_t( T_t( A_t( S_t( X_<=t ) ) ) ) pipeline."""

        # 1. S_t (Asset Selection Module)
        eligible: List[str] = []
        rejected: Dict[str, str] = {}

        for a in candidate_assets:
            sym = a.get("symbol", "").upper().strip()
            volume = float(a.get("volume", 1_000_000))
            is_halted = bool(a.get("is_halted", False))

            if is_halted:
                rejected[sym] = "Asset trading is halted by venue"
            elif volume < 100_000:
                rejected[sym] = "Insufficient daily liquidity volume (<100k)"
            else:
                eligible.append(sym)

        s_t = AssetSelectionOutput(eligible_universe=eligible, rejected_assets=rejected)

        # 2. A_t (Asset Allocation Module)
        raw_weights: Dict[str, float] = {}
        n = len(eligible)

        if n > 0:
            if target_allocation_method == "risk_parity":
                # Inverse-volatility weighting approximation
                inv_vols = {}
                for sym in eligible:
                    vol = next((float(a.get("volatility", 0.25)) for a in candidate_assets if a.get("symbol") == sym), 0.25)
                    inv_vols[sym] = 1.0 / max(0.05, vol)
                sum_inv = sum(inv_vols.values())
                raw_weights = {sym: round(inv_vols[sym] / sum_inv, 4) for sym in eligible}
            else:
                # Equal Weight
                eq_w = round(1.0 / n, 4)
                raw_weights = {sym: eq_w for sym in eligible}

        a_t = RawAllocationOutput(method=target_allocation_method, raw_weights=raw_weights)

        # 3. T_t (Execution Timing Module - Volatility Scaling)
        if volatility_index_vix > 35.0:
            regime = "extreme_crisis"
            multiplier = 0.35  # Hold 65% cash
        elif volatility_index_vix > 24.0:
            regime = "high_vol_stress"
            multiplier = 0.65  # Hold 35% cash
        elif volatility_index_vix > 18.0:
            regime = "normal"
            multiplier = 0.90
        else:
            regime = "low_vol"
            multiplier = 1.00

        timed_weights = {sym: round(w * multiplier, 4) for sym, w in raw_weights.items()}
        t_t = TimingAdjustmentOutput(
            market_volatility_regime=regime,
            exposure_multiplier=multiplier,
            timed_weights=timed_weights,
        )

        # 4. R_t (Real-Time Risk Overlay Module)
        violations: List[str] = []
        final_weights: Dict[str, float] = {}

        for sym, w in timed_weights.items():
            if w > max_single_weight:
                violations.append(f"{sym} weight ({w*100:.1f}%) clamped to max limit ({max_single_weight*100:.1f}%)")
                final_weights[sym] = max_single_weight
            else:
                final_weights[sym] = w

        total_invested = sum(final_weights.values())
        if total_invested > 1.0:
            scale_down = 1.0 / total_invested
            violations.append("Total leverage exceeded 100%; proportionally scaled down")
            final_weights = {sym: round(w * scale_down, 4) for sym, w in final_weights.items()}
            total_invested = sum(final_weights.values())

        cash_pct = round((1.0 - total_invested) * 100.0, 2)

        r_t = RiskOverlayOutput(
            max_single_asset_cap=max_single_weight,
            max_total_leverage=1.0,
            cash_buffer_pct=cash_pct,
            violations_corrected=violations,
            final_contract_weights=final_weights,
        )

        return FinRLWeightPipelineResult(
            timestamp_t=datetime.now(timezone.utc).isoformat(),
            selection_s_t=s_t,
            allocation_a_t=a_t,
            timing_t_t=t_t,
            risk_overlay_r_t=r_t,
            final_portfolio_weights=final_weights,
        )

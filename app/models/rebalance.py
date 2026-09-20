"""Target allocations and non-executing rebalance recommendations."""
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field as PField, model_validator

from app.core.money import Money, ZERO


class AllocationTarget(BaseModel):
    symbol: str = PField(min_length=1, max_length=20)
    target_weight_pct: Money = PField(gt=0, le=100)


class RebalanceRequest(BaseModel):
    targets: list[AllocationTarget] = PField(min_length=1, max_length=30)

    @model_validator(mode="after")
    def _weights_total_100(self) -> "RebalanceRequest":
        symbols = [t.symbol.upper().strip() for t in self.targets]
        if len(set(symbols)) != len(symbols):
            raise ValueError("Ayni sembol birden fazla hedefte bulunamaz")
        if sum((t.target_weight_pct for t in self.targets), ZERO) != Decimal("100"):
            raise ValueError("Hedef agirliklar tam %100 olmali")
        return self


class RebalanceAction(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    quantity: Money
    reference_price: Money
    current_weight_pct: Money
    target_weight_pct: Money
    notional_delta: Money


class RebalancePlan(BaseModel):
    total_value: Money
    actions: list[RebalanceAction] = []
    disclaimer: str = "Recommendation only; review before execution."

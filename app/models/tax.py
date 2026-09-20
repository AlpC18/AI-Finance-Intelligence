"""Tax-lot views reconstructed from the immutable transaction ledger."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.core.money import Money

LotMethod = Literal["FIFO", "LIFO"]


class OpenTaxLot(BaseModel):
    symbol: str
    acquired_at: datetime
    quantity_remaining: Money
    cost_per_unit: Money


class RealizedTaxLot(BaseModel):
    symbol: str
    acquired_at: datetime
    sold_at: datetime
    quantity: Money
    cost_basis: Money
    proceeds: Money
    gain_loss: Money
    term: Literal["short", "long"]


class TaxLotReport(BaseModel):
    year: int
    method: LotMethod
    realized: list[RealizedTaxLot] = []
    open_lots: list[OpenTaxLot] = []
    short_term_gain_loss: Money
    long_term_gain_loss: Money
    total_gain_loss: Money

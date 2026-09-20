"""Exact arithmetic for money, and the boundaries where it starts and stops.

Binary floats cannot represent most decimal fractions: ``0.1 + 0.1 + 0.1`` is
``0.30000000000000004``. For an indicator that is noise well below the signal.
For a ledger it is a defect that compounds - every fill, every mark-to-market,
every realised P&L carries a little more error, and the daily-loss limit that
halts an account is a comparison against a number built from all of them.

WHAT IS MONEY (use ``Money`` / ``to_decimal``)
    Cash, prices, quantities, notionals, equity, realised and unrealised P&L,
    drawdown and the loss-limit percentages compared against it. Anything
    produced by *exact arithmetic over other money*.

WHAT IS NOT (leave as ``float``)
    Statistics computed over a series - RSI and the other indicators, Sharpe,
    volatility, VaR, correlations, optimiser weights - and AI signal
    confidence. These come out of numpy/pandas as float64, carry genuine
    modelling error orders of magnitude larger than float epsilon, and forcing
    Decimal on them buys nothing while breaking numpy interop.

    Backtests sit on the float side deliberately: a simulation over a pandas
    price series is an *estimate*, not an account, and its P&L is never
    settled against anything.

THE WIRE
    ``Money`` serialises to a JSON number, not a string, so the published API
    contract is unchanged. Exactness is kept everywhere it can affect a
    decision and spent only at the presentation boundary, after every
    calculation is already done.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Annotated, Any, Optional

from pydantic import PlainSerializer

# 8 decimal places accommodates fractional-share quantities and sub-cent
# prices without ever reaching the precision limit of the column.
MONEY_DIGITS = 28
MONEY_PLACES = 8
_QUANTUM = Decimal(1).scaleb(-MONEY_PLACES)  # 0.00000001

ZERO = Decimal("0")
HUNDRED = Decimal("100")  # percentage conversions stay in exact arithmetic


def to_decimal(value: Any, default: Optional[Decimal] = None) -> Decimal:
    """Parse anything a provider or a client might hand us into an exact Decimal.

    Brokers are inconsistent: Alpaca returns ``"19.99"`` as a string on some
    fields and ``19.99`` as a JSON number on others. A float is converted via
    ``str`` rather than ``Decimal(float)`` on purpose - ``Decimal(0.1)`` is
    ``0.1000000000000000055511151231257827021181583404541015625``, faithfully
    preserving the very error we are here to remove, whereas
    ``Decimal(str(0.1))`` is ``0.1``.
    """
    if value is None:
        return ZERO if default is None else default
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):  # bool is an int subclass; never money
        raise TypeError("bool is not a monetary value")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ZERO if default is None else default
        try:
            return Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"not a monetary value: {value!r}") from exc
    raise TypeError(f"cannot interpret {type(value).__name__} as money")


def opt_decimal(value: Any) -> Optional[Decimal]:
    """``to_decimal`` that preserves None instead of coercing it to zero.

    The distinction matters: an unfilled order has ``filled_avg_price = None``
    ("no fill happened"), which is not the same claim as ``0`` ("it filled at
    nothing").
    """
    return None if value is None else to_decimal(value)


def quantize(value: Decimal) -> Decimal:
    """Round to the storage scale, half-up - the convention finance expects.

    Python's default is banker's rounding (half-to-even), which is correct for
    statistics and surprising on an invoice.
    """
    return value.quantize(_QUANTUM, rounding=ROUND_HALF_UP)


def _serialize(value: Optional[Decimal]) -> Optional[float]:
    return None if value is None else float(value)


# Exact in Python and in the database; a plain JSON number on the wire.
Money = Annotated[
    Decimal,
    PlainSerializer(_serialize, return_type=float, when_used="json"),
]

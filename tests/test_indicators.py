import numpy as np
import pandas as pd

from app.services.indicators import annual_volatility, compute_indicators


def test_compute_indicators_full_series():
    close = pd.Series(np.linspace(100, 140, 80))
    ind = compute_indicators(close)
    assert ind.sma_20 is not None
    assert ind.sma_50 is not None
    assert ind.rsi_14 is not None
    assert 0 <= ind.rsi_14 <= 100
    assert ind.macd is not None


def test_indicators_short_series_returns_none():
    close = pd.Series([100.0, 101.0, 102.0])
    ind = compute_indicators(close)
    assert ind.sma_50 is None
    assert ind.rsi_14 is None


def test_annual_volatility_constant_series_is_zero():
    assert annual_volatility(pd.Series([10.0] * 30)) == 0.0

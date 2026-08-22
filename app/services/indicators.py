"""Pure technical-indicator functions over a Close price series."""
import numpy as np
import pandas as pd

from app.models.market import Indicators


def _rsi(close: pd.Series, window: int = 14) -> float | None:
    if len(close) < window + 1:
        return None
    delta = close.diff()
    avg_gain = delta.clip(lower=0).rolling(window).mean().iloc[-1]
    avg_loss = (-delta.clip(upper=0)).rolling(window).mean().iloc[-1]
    if pd.isna(avg_gain) or pd.isna(avg_loss):
        return None
    if avg_loss == 0:
        # No losses in the window -> fully overbought (or flat if no gains).
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return round(float(100 - (100 / (1 + rs))), 2)


def _sma(close: pd.Series, window: int) -> float | None:
    if len(close) < window:
        return None
    return round(float(close.rolling(window).mean().iloc[-1]), 4)


def _ema(close: pd.Series, span: int) -> float | None:
    if len(close) < span:
        return None
    return round(float(close.ewm(span=span, adjust=False).mean().iloc[-1]), 4)


def _macd(close: pd.Series) -> tuple[float | None, float | None]:
    if len(close) < 26:
        return None, None
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    return round(float(macd_line.iloc[-1]), 4), round(float(signal_line.iloc[-1]), 4)


def annual_volatility(close: pd.Series) -> float | None:
    if len(close) < 2:
        return None
    daily = close.pct_change().dropna()
    if daily.empty:
        return None
    return round(float(daily.std() * np.sqrt(252)), 4)


def compute_indicators(close: pd.Series) -> Indicators:
    """Compute a bundle of indicators from a Close price series."""
    macd_line, macd_signal = _macd(close)
    return Indicators(
        rsi_14=_rsi(close),
        sma_20=_sma(close, 20),
        sma_50=_sma(close, 50),
        ema_12=_ema(close, 12),
        macd=macd_line,
        macd_signal=macd_signal,
        volatility_annual=annual_volatility(close),
    )

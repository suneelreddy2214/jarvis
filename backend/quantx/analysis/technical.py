"""Technical analysis indicators and signal generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd

from quantx.core.models import MarketDirection, Side


@dataclass
class IndicatorSnapshot:
    close: float
    ema_9: float
    ema_21: float
    ema_50: float
    ema_200: float
    sma_20: float
    sma_50: float
    rsi: float
    macd: float
    macd_signal: float
    macd_hist: float
    adx: float
    atr: float
    bb_upper: float
    bb_mid: float
    bb_lower: float
    supertrend: float
    supertrend_dir: int  # 1 bullish, -1 bearish
    vwap: Optional[float]
    volume: float
    avg_volume: float
    volume_ratio: float
    pivot: float
    r1: float
    s1: float
    trend: MarketDirection
    momentum: str  # strong_up | up | neutral | down | strong_down
    supporting: list[str] = field(default_factory=list)


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    atr = _atr(df, period)
    plus_di = 100 * pd.Series(plus_dm, index=df.index).rolling(period).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(period).mean() / atr
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)) * 100
    return dx.rolling(period).mean()


def _bollinger(close: pd.Series, period: int = 20, std: float = 2.0):
    mid = _sma(close, period)
    rolling_std = close.rolling(period).std()
    upper = mid + std * rolling_std
    lower = mid - std * rolling_std
    return upper, mid, lower


def _supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0):
    atr = _atr(df, period)
    hl2 = (df["high"] + df["low"]) / 2
    upper = hl2 + multiplier * atr
    lower = hl2 - multiplier * atr
    st = pd.Series(index=df.index, dtype=float)
    direction = pd.Series(index=df.index, dtype=int)
    st.iloc[0] = upper.iloc[0]
    direction.iloc[0] = 1
    for i in range(1, len(df)):
        if df["close"].iloc[i] > upper.iloc[i - 1]:
            direction.iloc[i] = 1
        elif df["close"].iloc[i] < lower.iloc[i - 1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i - 1]
        if direction.iloc[i] == 1:
            st.iloc[i] = max(lower.iloc[i], st.iloc[i - 1]) if direction.iloc[i - 1] == 1 else lower.iloc[i]
        else:
            st.iloc[i] = min(upper.iloc[i], st.iloc[i - 1]) if direction.iloc[i - 1] == -1 else upper.iloc[i]
    return st, direction


def _pivots(df: pd.DataFrame) -> tuple[float, float, float]:
    prev = df.iloc[-2] if len(df) >= 2 else df.iloc[-1]
    pivot = (prev["high"] + prev["low"] + prev["close"]) / 3
    r1 = 2 * pivot - prev["low"]
    s1 = 2 * pivot - prev["high"]
    return float(pivot), float(r1), float(s1)


def _vwap(df: pd.DataFrame) -> Optional[float]:
    if "volume" not in df.columns or df["volume"].sum() == 0:
        return None
    typical = (df["high"] + df["low"] + df["close"]) / 3
    return float((typical * df["volume"]).sum() / df["volume"].sum())


class TechnicalAnalyzer:
    """Compute indicators and derive trend/momentum/volume confirmation."""

    def analyze(self, df: pd.DataFrame) -> IndicatorSnapshot:
        if df is None or len(df) < 50:
            raise ValueError("Need at least 50 bars of OHLC data")

        df = df.copy()
        for col in ("open", "high", "low", "close", "volume"):
            if col not in df.columns:
                raise ValueError(f"Missing column: {col}")

        close = df["close"]
        ema9 = _ema(close, 9)
        ema21 = _ema(close, 21)
        ema50 = _ema(close, 50)
        ema200 = _ema(close, 200) if len(df) >= 200 else _ema(close, min(200, len(df)))
        sma20 = _sma(close, 20)
        sma50 = _sma(close, 50)
        rsi = _rsi(close, 14)
        macd, macd_sig, macd_hist = _macd(close)
        adx = _adx(df, 14)
        atr = _atr(df, 14)
        bb_u, bb_m, bb_l = _bollinger(close)
        st, st_dir = _supertrend(df)
        pivot, r1, s1 = _pivots(df)
        vwap = _vwap(df.tail(min(78, len(df))))  # approx session for daily use last N

        i = -1
        avg_vol = float(df["volume"].tail(20).mean())
        vol = float(df["volume"].iloc[i])
        vol_ratio = vol / avg_vol if avg_vol > 0 else 1.0

        c = float(close.iloc[i])
        e9, e21, e50, e200 = float(ema9.iloc[i]), float(ema21.iloc[i]), float(ema50.iloc[i]), float(ema200.iloc[i])
        rsi_v = float(rsi.iloc[i]) if not np.isnan(rsi.iloc[i]) else 50.0
        adx_v = float(adx.iloc[i]) if not np.isnan(adx.iloc[i]) else 0.0
        atr_v = float(atr.iloc[i]) if not np.isnan(atr.iloc[i]) else 0.0
        macd_v = float(macd.iloc[i]) if not np.isnan(macd.iloc[i]) else 0.0
        macd_sig_v = float(macd_sig.iloc[i]) if not np.isnan(macd_sig.iloc[i]) else 0.0
        macd_hist_v = float(macd_hist.iloc[i]) if not np.isnan(macd_hist.iloc[i]) else 0.0
        st_v = float(st.iloc[i]) if not np.isnan(st.iloc[i]) else c
        st_d = int(st_dir.iloc[i])

        supporting: list[str] = []
        bull_score = 0
        bear_score = 0

        # Trend structure
        if e9 > e21 > e50:
            bull_score += 2
            supporting.append("EMA stack bullish (9>21>50)")
        elif e9 < e21 < e50:
            bear_score += 2
            supporting.append("EMA stack bearish (9<21<50)")

        if c > e200:
            bull_score += 1
            supporting.append("Price above EMA200")
        else:
            bear_score += 1
            supporting.append("Price below EMA200")

        if st_d == 1:
            bull_score += 1
            supporting.append(f"SuperTrend bullish @ {st_v:.2f}")
        else:
            bear_score += 1
            supporting.append(f"SuperTrend bearish @ {st_v:.2f}")

        # Momentum
        if rsi_v > 55:
            bull_score += 1
            supporting.append(f"RSI {rsi_v:.1f} bullish momentum")
        elif rsi_v < 45:
            bear_score += 1
            supporting.append(f"RSI {rsi_v:.1f} bearish momentum")
        else:
            supporting.append(f"RSI {rsi_v:.1f} neutral")

        if macd_hist_v > 0 and macd_v > macd_sig_v:
            bull_score += 1
            supporting.append("MACD histogram positive")
        elif macd_hist_v < 0 and macd_v < macd_sig_v:
            bear_score += 1
            supporting.append("MACD histogram negative")

        if adx_v >= 25:
            supporting.append(f"ADX {adx_v:.1f} — trend strength confirmed")
            if bull_score > bear_score:
                bull_score += 1
            elif bear_score > bull_score:
                bear_score += 1
        else:
            supporting.append(f"ADX {adx_v:.1f} — weak/no trend")

        # Volume
        if vol_ratio >= 1.2:
            supporting.append(f"Volume confirmation ({vol_ratio:.2f}x avg)")
            if bull_score > bear_score:
                bull_score += 1
            elif bear_score > bull_score:
                bear_score += 1
        else:
            supporting.append(f"Volume muted ({vol_ratio:.2f}x avg)")

        # VWAP
        if vwap:
            if c > vwap:
                bull_score += 1
                supporting.append(f"Above VWAP ({vwap:.2f})")
            else:
                bear_score += 1
                supporting.append(f"Below VWAP ({vwap:.2f})")

        if bull_score - bear_score >= 3:
            trend = MarketDirection.BULLISH
        elif bear_score - bull_score >= 3:
            trend = MarketDirection.BEARISH
        elif adx_v < 20:
            trend = MarketDirection.RANGE_BOUND
        else:
            trend = MarketDirection.NEUTRAL

        if rsi_v >= 70 and macd_hist_v > 0:
            momentum = "strong_up"
        elif rsi_v >= 55:
            momentum = "up"
        elif rsi_v <= 30 and macd_hist_v < 0:
            momentum = "strong_down"
        elif rsi_v <= 45:
            momentum = "down"
        else:
            momentum = "neutral"

        return IndicatorSnapshot(
            close=c,
            ema_9=e9,
            ema_21=e21,
            ema_50=e50,
            ema_200=e200,
            sma_20=float(sma20.iloc[i]),
            sma_50=float(sma50.iloc[i]),
            rsi=rsi_v,
            macd=macd_v,
            macd_signal=macd_sig_v,
            macd_hist=macd_hist_v,
            adx=adx_v,
            atr=atr_v,
            bb_upper=float(bb_u.iloc[i]),
            bb_mid=float(bb_m.iloc[i]),
            bb_lower=float(bb_l.iloc[i]),
            supertrend=st_v,
            supertrend_dir=st_d,
            vwap=vwap,
            volume=vol,
            avg_volume=avg_vol,
            volume_ratio=vol_ratio,
            pivot=pivot,
            r1=r1,
            s1=s1,
            trend=trend,
            momentum=momentum,
            supporting=supporting,
        )

    def suggest_side(self, snap: IndicatorSnapshot) -> Optional[Side]:
        if snap.trend == MarketDirection.BULLISH and snap.momentum in ("up", "strong_up"):
            return Side.BUY
        if snap.trend == MarketDirection.BEARISH and snap.momentum in ("down", "strong_down"):
            return Side.SELL
        return None

    def levels_for_trade(
        self, snap: IndicatorSnapshot, side: Side, atr_stop_mult: float = 1.5
    ) -> dict[str, float]:
        atr = max(snap.atr, snap.close * 0.005)
        entry = snap.close
        if side == Side.BUY:
            stop = min(entry - atr_stop_mult * atr, snap.s1 if snap.s1 < entry else entry - atr_stop_mult * atr)
            risk = entry - stop
            t1 = entry + 2 * risk
            t2 = entry + 3 * risk
        else:
            stop = max(entry + atr_stop_mult * atr, snap.r1 if snap.r1 > entry else entry + atr_stop_mult * atr)
            risk = stop - entry
            t1 = entry - 2 * risk
            t2 = entry - 3 * risk
        rr = abs(t1 - entry) / risk if risk > 0 else 0
        return {"entry": entry, "stop_loss": stop, "target_1": t1, "target_2": t2, "risk_reward": rr}

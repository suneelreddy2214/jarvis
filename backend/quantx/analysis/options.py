"""Options chain analysis helpers (OI, PCR, max pain, IV heuristics)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd


@dataclass
class OptionsSummary:
    pcr: Optional[float]
    max_pain: Optional[float]
    iv_rank: Optional[float]
    sentiment: str
    summary: str
    details: dict[str, Any]


class OptionsAnalyzer:
    """
    Analyze option chain dataframes with columns:
    strike, call_oi, put_oi, call_iv, put_iv, call_ltp, put_ltp
    """

    def analyze(
        self,
        chain: Optional[pd.DataFrame],
        spot: Optional[float] = None,
    ) -> OptionsSummary:
        if chain is None or chain.empty:
            return OptionsSummary(
                pcr=None,
                max_pain=None,
                iv_rank=None,
                sentiment="unavailable",
                summary="Option chain data unavailable — no options bias applied.",
                details={},
            )

        df = chain.copy()
        call_oi = float(df["call_oi"].sum()) if "call_oi" in df else 0.0
        put_oi = float(df["put_oi"].sum()) if "put_oi" in df else 0.0
        pcr = (put_oi / call_oi) if call_oi > 0 else None

        max_pain = self._max_pain(df)
        iv_rank = None
        if "call_iv" in df.columns and "put_iv" in df.columns:
            ivs = pd.concat([df["call_iv"], df["put_iv"]]).dropna()
            if len(ivs) > 0:
                # Without history, approximate IV "rank" via dispersion around median
                med = float(ivs.median())
                mx = float(ivs.max())
                mn = float(ivs.min())
                iv_rank = ((med - mn) / (mx - mn) * 100) if mx > mn else 50.0

        if pcr is None:
            sentiment = "neutral"
            summary = "Insufficient OI for PCR."
        elif pcr >= 1.2:
            sentiment = "bullish_hedge"
            summary = f"PCR {pcr:.2f} elevated — put-heavy (often contrarian bullish near extremes)."
        elif pcr <= 0.7:
            sentiment = "bearish_caution"
            summary = f"PCR {pcr:.2f} low — call-heavy (caution for longs)."
        else:
            sentiment = "neutral"
            summary = f"PCR {pcr:.2f} balanced."

        if max_pain and spot:
            summary += f" Max pain ≈ {max_pain:.0f} (spot {spot:.0f})."

        if iv_rank is not None:
            summary += f" IV proxy rank {iv_rank:.0f}."

        return OptionsSummary(
            pcr=round(pcr, 3) if pcr is not None else None,
            max_pain=max_pain,
            iv_rank=round(iv_rank, 1) if iv_rank is not None else None,
            sentiment=sentiment,
            summary=summary,
            details={"call_oi": call_oi, "put_oi": put_oi},
        )

    def _max_pain(self, df: pd.DataFrame) -> Optional[float]:
        if "strike" not in df.columns:
            return None
        strikes = df["strike"].astype(float)
        call_oi = df.get("call_oi", pd.Series(0, index=df.index)).astype(float)
        put_oi = df.get("put_oi", pd.Series(0, index=df.index)).astype(float)
        pains = []
        for strike in strikes:
            call_pain = ((strikes - strike).clip(lower=0) * call_oi).sum()
            put_pain = ((strike - strikes).clip(lower=0) * put_oi).sum()
            pains.append((float(strike), float(call_pain + put_pain)))
        if not pains:
            return None
        return min(pains, key=lambda x: x[1])[0]

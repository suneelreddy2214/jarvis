"""Lightweight fundamental scoring for Indian equities (Yahoo info + heuristics)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class FundamentalScore:
    score: float  # 0-100
    summary: str
    metrics: dict[str, Any]
    data_quality: str = "unknown"  # rich | partial | sparse


class FundamentalAnalyzer:
    """
    Scores fundamentals from Yahoo metadata.
    Sparse data is scored below the equity entry floor so paper swing trades
    cannot rely on technicals alone.
    """

    def analyze(self, symbol: str, info: Optional[dict] = None) -> FundamentalScore:
        info = info or {}
        metrics: dict[str, Any] = {}
        score = 55.0
        notes: list[str] = []

        pe = info.get("trailingPE") or info.get("pe")
        pb = info.get("priceToBook") or info.get("pb")
        roe = info.get("returnOnEquity")
        de = info.get("debtToEquity")
        margin = info.get("profitMargins")
        growth = info.get("revenueGrowth")
        sector = info.get("sector")
        industry = info.get("industry")
        mcap = info.get("marketCap")
        div = info.get("dividendYield")

        if sector:
            metrics["sector"] = sector
            notes.append(f"Sector {sector}")
        if industry:
            metrics["industry"] = industry
        if mcap is not None:
            try:
                metrics["market_cap_cr"] = round(float(mcap) / 1e7, 1)
            except Exception:
                pass
        if div is not None:
            try:
                d = float(div) * 100 if abs(float(div)) <= 1 else float(div)
                metrics["dividend_yield"] = round(d, 2)
                if d >= 2:
                    score += 3
            except Exception:
                pass

        if pe is not None:
            metrics["pe"] = round(float(pe), 2)
            if 0 < pe < 25:
                score += 8
                notes.append(f"PE {pe:.1f} reasonable")
            elif pe >= 40:
                score -= 10
                notes.append(f"PE {pe:.1f} elevated")
            elif pe <= 0:
                score -= 5
                notes.append("Negative PE")

        if pb is not None:
            metrics["pb"] = round(float(pb), 2)
            if 0 < pb < 3:
                score += 5
                notes.append(f"PB {pb:.1f} acceptable")
            elif pb >= 6:
                score -= 5
                notes.append(f"PB {pb:.1f} rich")

        if roe is not None:
            roe_pct = float(roe) * 100 if abs(float(roe)) <= 1 else float(roe)
            metrics["roe"] = round(roe_pct, 2)
            if roe_pct >= 15:
                score += 10
                notes.append(f"ROE {roe_pct:.1f}% strong")
            elif roe_pct < 8:
                score -= 8
                notes.append(f"ROE {roe_pct:.1f}% weak")

        if de is not None:
            metrics["debt_equity"] = round(float(de), 2)
            if de < 1:
                score += 5
                notes.append("Low leverage")
            elif de > 2:
                score -= 8
                notes.append("High leverage")

        if margin is not None:
            m = float(margin) * 100 if abs(float(margin)) <= 1 else float(margin)
            metrics["profit_margin"] = round(m, 2)
            if m >= 12:
                score += 5
            elif m < 5:
                score -= 5

        if growth is not None:
            g = float(growth) * 100 if abs(float(growth)) <= 1 else float(growth)
            metrics["revenue_growth"] = round(g, 2)
            if g >= 15:
                score += 8
                notes.append(f"Revenue growth {g:.1f}%")
            elif g < 0:
                score -= 10
                notes.append("Revenue contraction")

        numeric_keys = {"pe", "pb", "roe", "debt_equity", "profit_margin", "revenue_growth"}
        numeric_count = sum(1 for k in numeric_keys if k in metrics)

        if numeric_count == 0:
            # Sparse fundamentals must not pass equity swing floor (min_fundamental_score)
            notes.append(
                "Sparse fundamental data — block tech-only equity entries; size conservatively if F&O"
            )
            score = 40.0
            data_quality = "sparse"
        elif numeric_count < 3:
            notes.append("Partial fundamentals — demand stronger technical confluence")
            score = min(score, 58.0)
            data_quality = "partial"
        else:
            data_quality = "rich"

        score = max(0.0, min(100.0, score))
        summary = f"{symbol}: score {score:.0f}/100 ({data_quality}). " + (
            "; ".join(notes) if notes else "Neutral."
        )
        return FundamentalScore(
            score=score, summary=summary, metrics=metrics, data_quality=data_quality
        )

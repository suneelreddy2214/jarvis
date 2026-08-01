"""Daily / weekly reporting."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from quantx.core.models import Report
from quantx.core.engine import QuantXEngine
from quantx.data.market_data import DEFAULT_WATCHLIST, MarketDataService
from quantx.portfolio.manager import PortfolioManager


class ReportGenerator:
    def __init__(
        self,
        portfolio: Optional[PortfolioManager] = None,
        engine: Optional[QuantXEngine] = None,
        data: Optional[MarketDataService] = None,
    ):
        self.portfolio = portfolio or PortfolioManager()
        self.engine = engine or QuantXEngine(risk_manager=self.portfolio.risk)
        self.data = data or MarketDataService()

    def morning_report(self) -> Report:
        macro = self.data.get_macro_snapshot()
        snap = self.portfolio.snapshot()
        return Report(
            report_type="morning",
            title="QuantX Morning Report",
            summary=(
                f"Capital ₹{snap.capital:,.0f} | Mode {snap.mode} | "
                f"Halted={snap.trading_halted} | Macro Nifty {macro.get('nifty_change_pct', 'n/a')}%, "
                f"VIX {macro.get('india_vix', 'n/a')}"
            ),
            sections={
                "portfolio": snap.model_dump(),
                "macro": macro,
                "risk_reminder": (
                    "Max 1% risk/trade · Max 2% daily loss · Max 5% weekly loss · "
                    "Stop after 3 consecutive losses · Never average losers · Never remove stops"
                ),
                "checklist": [
                    "Review overnight global cues (US futures, SGX/GIFT Nifty)",
                    "Check economic calendar for RBI / inflation / results",
                    "Scan watchlist only after risk gates green",
                    "Confirm India VIX regime before sizing",
                ],
            },
        )

    def market_open_report(self) -> Report:
        snap = self.portfolio.snapshot()
        return Report(
            report_type="market_open",
            title="QuantX Market Open Report",
            summary=f"Session focus: capital protection. Open positions: {snap.open_positions}",
            sections={
                "portfolio": snap.model_dump(),
                "actions": [
                    "Validate gap risk on open positions",
                    "Update stops — never widen",
                    "Only take A+ setups with RR ≥ 1:2",
                ],
            },
        )

    def intraday_report(self) -> Report:
        positions = [p.model_dump(mode="json") for p in self.portfolio.mark_to_market()]
        snap = self.portfolio.snapshot()
        return Report(
            report_type="intraday",
            title="QuantX Intraday Report",
            summary=(
                f"Unrealized ₹{snap.unrealized_pnl:,.0f} | Daily realized ₹{snap.realized_pnl_today:,.0f} | "
                f"Drawdown {snap.drawdown_pct:.2f}%"
            ),
            sections={"portfolio": snap.model_dump(), "positions": positions},
        )

    def closing_report(self) -> Report:
        snap = self.portfolio.snapshot()
        journal = [j.model_dump(mode="json") for j in self.portfolio.db.list_journal(20)]
        return Report(
            report_type="closing",
            title="QuantX Closing Report",
            summary=f"Day PnL ₹{snap.realized_pnl_today:,.0f} | Total PnL ₹{snap.total_pnl:,.0f}",
            sections={
                "portfolio": snap.model_dump(),
                "journal_today": journal,
                "discipline_score": self._discipline_score(snap),
            },
        )

    def weekly_review(self) -> Report:
        snap = self.portfolio.snapshot()
        journal = self.portfolio.db.list_journal(200)
        wins = [j for j in journal if j.pnl > 0]
        losses = [j for j in journal if j.pnl <= 0]
        win_rate = len(wins) / len(journal) * 100 if journal else 0
        avg_win = sum(j.pnl for j in wins) / len(wins) if wins else 0
        avg_loss = sum(j.pnl for j in losses) / len(losses) if losses else 0
        return Report(
            report_type="weekly",
            title="QuantX Weekly Review",
            summary=f"Win rate {win_rate:.1f}% | Avg win ₹{avg_win:,.0f} | Avg loss ₹{avg_loss:,.0f}",
            sections={
                "portfolio": snap.model_dump(),
                "stats": {
                    "trades": len(journal),
                    "wins": len(wins),
                    "losses": len(losses),
                    "win_rate": round(win_rate, 2),
                    "avg_win": round(avg_win, 2),
                    "avg_loss": round(avg_loss, 2),
                    "expectancy": round(avg_win * (win_rate / 100) + avg_loss * (1 - win_rate / 100), 2)
                    if journal else 0,
                },
                "lessons": [j.lessons for j in journal[:10] if j.lessons],
                "improvements": [
                    "Cut size when VIX elevated",
                    "Skip trades without volume confirmation",
                    "Review any rule breaches immediately",
                ],
            },
        )

    def risk_analysis(self) -> Report:
        snap = self.portfolio.snapshot()
        risk = self.portfolio.risk.status()
        return Report(
            report_type="risk",
            title="QuantX Risk Analysis",
            summary=f"Can trade: {risk.can_trade} | DD {risk.drawdown_pct:.2f}%",
            sections={"portfolio": snap.model_dump(), "risk": risk.model_dump()},
        )

    def _discipline_score(self, snap) -> dict:
        score = 100
        notes = []
        if snap.consecutive_losses >= 3:
            score -= 30
            notes.append("Hit consecutive loss limit")
        if snap.kill_switch_active:
            score -= 20
            notes.append("Kill switch used")
        if snap.drawdown_pct > 5:
            score -= 20
            notes.append("Elevated drawdown")
        return {"score": max(0, score), "notes": notes}

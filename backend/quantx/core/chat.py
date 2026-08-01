"""
QuantX Chat Assistant — answers operator questions using live agent context.

No external LLM required. Optional OpenAI enhancement if QUANTX_OPENAI_API_KEY is set.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Optional


class QuantXChat:
    """Context-aware Q&A over paper trading state."""

    def __init__(self, context_provider):
        """
        context_provider: callable returning a dict with keys:
          portfolio, risk, positions, orders, cycles, performance, pnl, session, config, emergency
        """
        self._get_context = context_provider

    def ask(self, message: str, history: Optional[list[dict]] = None) -> dict:
        text = (message or "").strip()
        if not text:
            return self._reply("Ask me about P&L, positions, orders, risk, cycles, or how QuantX trades.")

        ctx = self._get_context()
        lower = text.lower()

        # Optional LLM path
        if os.getenv("QUANTX_OPENAI_API_KEY"):
            try:
                return self._ask_openai(text, ctx, history or [])
            except Exception as e:
                # Fall back to rule-based
                llm_note = f"(LLM unavailable: {e}) "

        else:
            llm_note = ""

        answer = self._route(lower, text, ctx)
        return self._reply(llm_note + answer, ctx)

    def _reply(self, content: str, ctx: Optional[dict] = None) -> dict:
        return {
            "role": "assistant",
            "content": content,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "sources": self._sources(ctx) if ctx else [],
        }

    def _sources(self, ctx: dict) -> list[str]:
        return [
            "portfolio",
            "risk",
            "positions",
            "orders",
            "cycles",
            "paper_session",
        ]

    def _route(self, lower: str, original: str, ctx: dict) -> str:
        if self._match(lower, ["help", "what can you", "commands", "how to ask"]):
            return self._help()

        if self._match(lower, ["how do you trade", "how does quantx", "how trading works", "explain agent", "how you work"]):
            return self._how_trading_works()

        if self._match(lower, ["risk", "limits", "drawdown", "kill switch", "can i trade", "can trade", "halt"]):
            return self._risk(ctx)

        if self._match(lower, ["pnl", "p&l", "profit", "loss", "performance", "returns", "fees"]):
            return self._pnl(ctx)

        if self._match(lower, ["position", "open trade", "holdings", "what am i holding"]):
            return self._positions(ctx)

        if self._match(lower, ["order", "fill", "filled", "execution"]):
            sym = self._extract_symbol(original, ctx)
            return self._orders(ctx, sym)

        if self._match(lower, ["cycle", "scan", "last cycle", "session", "running"]):
            return self._cycles(ctx)

        if self._match(lower, ["journal", "closed", "past trade", "history"]):
            return self._journal(ctx)

        if self._match(lower, ["capital", "margin", "balance", "cash"]):
            return self._capital(ctx)

        if self._match(lower, ["strategy", "strategies", "swing", "breakout"]):
            return self._strategies()

        if self._match(lower, ["why reject", "rejected", "why no trade", "why not"]):
            return self._rejections(ctx)

        if self._match(lower, ["emergency", "panic", "kill"]):
            return self._emergency(ctx)

        if self._match(lower, ["macro", "vix", "nifty", "market"]):
            return self._macro(ctx)

        # Symbol-specific
        sym = self._extract_symbol(original, ctx)
        if sym:
            return self._symbol_brief(sym, ctx)

        return (
            "I can clarify QuantX live state. Try asking:\n"
            "• What's my P&L?\n"
            "• Show open positions\n"
            "• Show orders / cycles\n"
            "• What are risk limits?\n"
            "• Why were trades rejected?\n"
            "• How does QuantX trade?\n"
            "• Status of RELIANCE\n"
            f"\nRight now: session={'RUNNING' if ctx.get('paper', {}).get('running') else 'IDLE'}, "
            f"open positions={ctx.get('portfolio', {}).get('open_positions', 0)}, "
            f"can_trade={ctx.get('risk', {}).get('can_trade')}."
        )

    def _match(self, lower: str, keys: list[str]) -> bool:
        return any(k in lower for k in keys)

    def _extract_symbol(self, text: str, ctx: dict) -> Optional[str]:
        known = set()
        for p in ctx.get("positions") or []:
            known.add(str(p.get("symbol", "")).upper())
        for o in ctx.get("orders") or []:
            known.add(str(o.get("symbol", "")).upper())
        for s in (ctx.get("watchlist_symbols") or []):
            known.add(str(s).upper())
        # Also common NSE names
        known.update(
            {
                "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN",
                "ITC", "LT", "AXISBANK", "BHARTIARTL",
            }
        )
        tokens = re.findall(r"[A-Za-z]{2,15}", text.upper())
        for t in tokens:
            if t in known:
                return t
        return None

    def _help(self) -> str:
        return (
            "**QuantX Chat** — ask for clarification on live paper trading.\n\n"
            "Examples:\n"
            "• What's my P&L today?\n"
            "• Show open positions and unrealized loss\n"
            "• List recent orders\n"
            "• Explain the last cycle\n"
            "• Why were trades rejected?\n"
            "• What are my risk limits?\n"
            "• How does QuantX decide entries?\n"
            "• Status of INFY\n"
        )

    def _how_trading_works(self) -> str:
        return (
            "QuantX paper trading loop:\n"
            "1. Every cycle — mark positions to market; exit on SL / trail / T1 / T2\n"
            "2. Check risk gates (1% per trade, 2% daily, 5% weekly, max DD, 3-loss stop)\n"
            "3. Pull NSE data via yfinance; run TA + strategy filters\n"
            "4. Size so stop risk ≤ 1% capital; require RR ≥ 1:2\n"
            "5. PaperBroker fills with slippage + fees (not live Zerodha unless configured)\n"
            "6. Never average into an existing symbol; never remove stops\n\n"
            "Capital preservation always comes first."
        )

    def _risk(self, ctx: dict) -> str:
        r = ctx.get("risk") or {}
        p = ctx.get("portfolio") or {}
        cfg = (ctx.get("config") or {}).get("risk") or {}
        reasons = r.get("reasons") or []
        return (
            f"**Risk status**\n"
            f"• Can trade: {'YES' if r.get('can_trade') else 'NO'}\n"
            f"• Drawdown: {r.get('drawdown_pct', p.get('drawdown_pct', 0)):.2f}% "
            f"(limit {cfg.get('max_drawdown_pct', 10)}%)\n"
            f"• Daily loss used: {r.get('daily_loss_used_pct', 0):.2f}% "
            f"(limit {cfg.get('max_daily_loss_pct', 2)}%)\n"
            f"• Consecutive losses: {r.get('consecutive_losses', 0)} "
            f"(halt at {cfg.get('max_consecutive_losses', 3)})\n"
            f"• Open positions: {r.get('open_positions', 0)} / {cfg.get('max_open_positions', 5)}\n"
            f"• Per-trade risk budget: ₹{r.get('risk_budget_remaining', 0):,.0f} "
            f"({cfg.get('max_risk_per_trade_pct', 1)}%)\n"
            f"• Halted: {p.get('trading_halted')} — {p.get('halt_reason') or 'none'}\n"
            + (f"• Blockers: {'; '.join(reasons)}\n" if reasons else "")
        )

    def _pnl(self, ctx: dict) -> str:
        s = (ctx.get("pnl") or {}).get("summary") or ctx.get("performance") or {}
        p = ctx.get("portfolio") or {}
        return (
            f"**P&L snapshot**\n"
            f"• Capital: ₹{s.get('capital', p.get('capital', 0)):,.2f}\n"
            f"• Total P&L: ₹{s.get('total_pnl', p.get('total_pnl', 0)):,.2f}\n"
            f"• Unrealized: ₹{s.get('unrealized_pnl', p.get('unrealized_pnl', 0)):,.2f}\n"
            f"• Realized (closed): ₹{s.get('closed_realized_pnl', p.get('realized_pnl_today', 0)):,.2f}\n"
            f"• Fees: ₹{s.get('total_fees', 0):,.2f}\n"
            f"• Win rate: {s.get('win_rate', 0):.1f}% "
            f"({s.get('wins', 0)}W / {s.get('losses', 0)}L)\n"
            f"• Open / Closed: {s.get('open_positions', p.get('open_positions', 0))} / "
            f"{s.get('closed_trades', 0)}\n"
            f"Open the **P&L** tab for order-level detail."
        )

    def _positions(self, ctx: dict) -> str:
        opens = [p for p in (ctx.get("positions") or []) if p.get("status") == "OPEN"]
        if not opens:
            opens = ctx.get("open_positions") or []
        if not opens:
            return "No open positions right now."
        lines = ["**Open positions**"]
        for p in opens:
            lines.append(
                f"• {p.get('symbol')} {p.get('side')} x{p.get('quantity')} "
                f"entry {p.get('entry_price')} LTP {p.get('current_price')} "
                f"PnL ₹{float(p.get('pnl') or 0):,.2f} ({float(p.get('pnl_pct') or 0):+.2f}%) "
                f"SL {p.get('stop_loss')} T1 {p.get('target_1')}"
            )
        return "\n".join(lines)

    def _orders(self, ctx: dict, symbol: Optional[str] = None) -> str:
        orders = ctx.get("orders") or []
        if symbol:
            orders = [o for o in orders if str(o.get("symbol", "")).upper() == symbol]
        if not orders:
            return f"No orders found{f' for {symbol}' if symbol else ''}."
        lines = [f"**Recent orders{f' — {symbol}' if symbol else ''}** (latest first)"]
        for o in orders[:15]:
            lines.append(
                f"• {o.get('created_at', '')[:19]} {o.get('symbol')} {o.get('side')} "
                f"x{o.get('quantity')} @ {o.get('price')} [{o.get('status')}] "
                f"{o.get('message', '')[:80]}"
            )
        return "\n".join(lines)

    def _cycles(self, ctx: dict) -> str:
        paper = ctx.get("paper") or {}
        cycles = ctx.get("cycles") or []
        lines = [
            f"**Paper session**: {'RUNNING' if paper.get('running') else 'IDLE'}",
            f"• Cycles: {paper.get('cycles', 0)} | Executed: {paper.get('executed', 0)} | "
            f"Rejected: {paper.get('rejected', 0)}",
            f"• Last: {paper.get('last_message', '—')}",
            "",
            "**Recent cycles**",
        ]
        if not cycles:
            lines.append("No cycle history yet. Click Start Auto or Run Cycle.")
        for c in cycles[:8]:
            lines.append(
                f"• #{c.get('cycle_no')} valid {c.get('valid_count')}/{c.get('scanned')} "
                f"filled {c.get('executed_count')} rej {c.get('rejected_count')} — {c.get('message')}"
            )
            for e in (c.get("executed") or [])[:5]:
                lines.append(
                    f"    FILL {e.get('symbol')} {e.get('side')} "
                    f"{e.get('quantity')}@{e.get('fill_price')}"
                )
            for r in (c.get("rejected") or [])[:5]:
                lines.append(f"    REJ {r.get('symbol')}: {r.get('reason')}")
        return "\n".join(lines)

    def _journal(self, ctx: dict) -> str:
        journal = ctx.get("journal") or []
        if not journal:
            return "Trade journal is empty — no closed trades logged yet."
        lines = ["**Closed trades (journal)**"]
        for j in journal[:12]:
            lines.append(
                f"• {j.get('symbol')} {j.get('side')} {j.get('quantity')} "
                f"{j.get('entry')} → {j.get('exit')} PnL ₹{float(j.get('pnl') or 0):,.2f} "
                f"| {j.get('market_condition') or j.get('lessons') or ''}"
            )
        return "\n".join(lines)

    def _capital(self, ctx: dict) -> str:
        p = ctx.get("portfolio") or {}
        return (
            f"**Capital**\n"
            f"• Capital: ₹{float(p.get('capital') or 0):,.2f}\n"
            f"• Available margin: ₹{float(p.get('available_margin') or 0):,.2f}\n"
            f"• Used margin: ₹{float(p.get('used_margin') or 0):,.2f}\n"
            f"• Mode: {p.get('mode', 'paper')}"
        )

    def _strategies(self) -> str:
        return (
            "**Strategies**\n"
            "• `swing_trend` — EMA stack + SuperTrend + ADX continuation\n"
            "• `breakout` — 20-day high/low break with volume expansion\n"
            "• `intraday_mean_reversion` — RSI/BB fades when ADX is weak\n\n"
            "All still require risk gates: ≤1% risk, RR ≥ 1:2, volume confirmation."
        )

    def _rejections(self, ctx: dict) -> str:
        cycles = ctx.get("cycles") or []
        rejected = []
        for c in cycles[:10]:
            for r in c.get("rejected") or []:
                rejected.append(f"Cycle #{c.get('cycle_no')}: {r.get('symbol')} — {r.get('reason')}")
        if not rejected:
            return (
                "No recent rejections logged. Common reject reasons:\n"
                "• Already in position (no averaging)\n"
                "• Max open positions / no free slot\n"
                "• Risk halt (drawdown / consecutive losses)\n"
                "• Duplicate order same day\n"
                "• Setup failed confidence / RR / volume gates"
            )
        return "**Recent rejections**\n" + "\n".join(f"• {x}" for x in rejected[:20])

    def _emergency(self, ctx: dict) -> str:
        e = ctx.get("emergency") or {}
        return (
            f"**Emergency controls**\n"
            f"• Kill switch: {e.get('kill_switch')}\n"
            f"• Max DD lock: {e.get('max_drawdown_lock')}\n"
            f"• Manual override: {e.get('manual_override')}\n"
            f"• Messages: {'; '.join(e.get('messages') or []) or 'none'}"
        )

    def _macro(self, ctx: dict) -> str:
        m = ctx.get("macro") or {}
        if not m:
            return "Macro snapshot unavailable right now."
        parts = [f"• {k}: {v}" for k, v in m.items()]
        return "**Macro**\n" + "\n".join(parts)

    def _symbol_brief(self, symbol: str, ctx: dict) -> str:
        lines = [f"**{symbol}**"]
        pos = [p for p in (ctx.get("positions") or []) if str(p.get("symbol", "")).upper() == symbol]
        ords = [o for o in (ctx.get("orders") or []) if str(o.get("symbol", "")).upper() == symbol]
        if pos:
            for p in pos:
                lines.append(
                    f"• Position {p.get('status')}: {p.get('side')} x{p.get('quantity')} "
                    f"entry {p.get('entry_price')} PnL ₹{float(p.get('pnl') or 0):,.2f}"
                )
        else:
            lines.append("• No open/closed position row matched in current book.")
        if ords:
            lines.append("• Recent orders:")
            for o in ords[:5]:
                lines.append(
                    f"  - {o.get('created_at', '')[:19]} {o.get('side')} "
                    f"x{o.get('quantity')} @ {o.get('price')} [{o.get('status')}]"
                )
        else:
            lines.append("• No orders for this symbol yet.")
        return "\n".join(lines)

    def _ask_openai(self, message: str, ctx: dict, history: list[dict]) -> dict:
        import json
        import urllib.request

        api_key = os.getenv("QUANTX_OPENAI_API_KEY")
        model = os.getenv("QUANTX_OPENAI_MODEL", "gpt-4o-mini")
        system = (
            "You are QuantX, an institutional paper-trading agent for NSE/BSE. "
            "Capital preservation first. Answer clearly using the provided live context JSON. "
            "If unsure, say so. Never encourage revenge trading or removing stops."
        )
        slim = {
            "portfolio": ctx.get("portfolio"),
            "risk": ctx.get("risk"),
            "paper": ctx.get("paper"),
            "positions": (ctx.get("positions") or [])[:10],
            "orders": (ctx.get("orders") or [])[:15],
            "cycles": (ctx.get("cycles") or [])[:5],
            "pnl_summary": (ctx.get("pnl") or {}).get("summary"),
        }
        messages = [{"role": "system", "content": system + "\n\nLIVE_CONTEXT:\n" + json.dumps(slim, default=str)[:12000]}]
        for h in history[-8:]:
            if h.get("role") in ("user", "assistant") and h.get("content"):
                messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": message})

        payload = json.dumps({"model": model, "messages": messages, "temperature": 0.2}).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        content = data["choices"][0]["message"]["content"]
        return self._reply(content, ctx)

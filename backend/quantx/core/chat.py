"""
QuantX LLM Chat — strategy/loss post-mortem reasoning.

LLM-first. Rule-based answers are only a fallback when no provider key is configured.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are QuantX, an institutional-grade trading coach embedded in a paper-trading system for NSE/BSE.

Primary mission: capital preservation first, then risk-adjusted returns.

Your job in chat is NOT to place orders. Your job is to:
1. Explain WHY trades made or lost money using the live context JSON.
2. Identify what the strategy/logic missed (entries, exits, filters, sizing, regime).
3. Point out process mistakes (averaging, ignoring volume, weak ADX, VIX spikes, etc.).
4. Suggest concrete rule improvements — never revenge trades or removing stops.
5. Be specific: cite symbols, prices, PnL, indicators, rejection reasons from context.
6. If context is missing data, say what is missing instead of inventing fills.

Hard rules you must respect and reinforce:
- Max 1% capital risk per trade
- Max 2% daily / 5% weekly loss
- Min RR 1:2
- Stop after 3 consecutive losses
- Never average losers / never remove stops

Answer in clear markdown. Prefer short sections:
- Verdict
- What happened
- What we missed
- Strategy/logic gaps
- Actionable fixes
"""


class LLMProviderError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class QuantXLLMChat:
    """LLM-based QuantX assistant with rich loss/strategy context."""

    def __init__(self, context_provider: Callable[[], dict], settings_store: Optional[Callable] = None):
        self._get_context = context_provider
        # settings_store(get|set) for persisted API key in SQLite
        self._store = settings_store

    # —— config ——
    def get_llm_config(self) -> dict:
        key = self._resolve_api_key()
        provider = self._resolve_provider()
        return {
            "enabled": bool(key),
            "provider": provider,
            "model": self._resolve_model(provider),
            "has_api_key": bool(key),
            "key_source": self._key_source(),
            "mode": "llm" if key else "fallback_rules",
            "supported_providers": ["openai", "groq", "openrouter"],
            "env_vars": [
                "QUANTX_LLM_PROVIDER",
                "QUANTX_OPENAI_API_KEY or OPENAI_API_KEY",
                "QUANTX_GROQ_API_KEY or GROQ_API_KEY",
                "QUANTX_OPENROUTER_API_KEY",
                "QUANTX_LLM_MODEL",
            ],
            "hint": (
                "Paste an API key in Chat settings (or set env). "
                "Groq free tier works well for loss post-mortems."
            ),
        }

    def set_api_key(self, api_key: str, provider: str = "groq", model: str = "") -> dict:
        api_key = (api_key or "").strip()
        provider = (provider or "groq").strip().lower()
        if provider not in ("openai", "groq", "openrouter"):
            raise ValueError("provider must be openai|groq|openrouter")
        if not self._store:
            raise RuntimeError("No settings store configured")
        if api_key:
            self._store("set", "llm_api_key", api_key)
            self._store("set", "llm_provider", provider)
            if model:
                self._store("set", "llm_model", model)
        else:
            self._store("set", "llm_api_key", "")
        return self.get_llm_config()

    def _store_get(self, key: str, default=None):
        if not self._store:
            return default
        return self._store("get", key, default)

    def _resolve_api_key(self) -> str:
        stored = self._store_get("llm_api_key") or ""
        if stored:
            return stored
        provider = self._resolve_provider()
        if provider == "groq":
            return os.getenv("QUANTX_GROQ_API_KEY") or os.getenv("GROQ_API_KEY") or ""
        if provider == "openrouter":
            return os.getenv("QUANTX_OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY") or ""
        return (
            os.getenv("QUANTX_OPENAI_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("QUANTX_GROQ_API_KEY")
            or os.getenv("GROQ_API_KEY")
            or ""
        )

    def _key_source(self) -> str:
        if self._store_get("llm_api_key"):
            return "dashboard"
        if os.getenv("QUANTX_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY"):
            return "env_openai"
        if os.getenv("QUANTX_GROQ_API_KEY") or os.getenv("GROQ_API_KEY"):
            return "env_groq"
        if os.getenv("QUANTX_OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY"):
            return "env_openrouter"
        return "none"

    def _resolve_provider(self) -> str:
        stored = (self._store_get("llm_provider") or "").lower()
        if stored in ("openai", "groq", "openrouter"):
            return stored
        env = (os.getenv("QUANTX_LLM_PROVIDER") or "").lower()
        if env in ("openai", "groq", "openrouter"):
            return env
        if os.getenv("QUANTX_GROQ_API_KEY") or os.getenv("GROQ_API_KEY"):
            return "groq"
        if os.getenv("QUANTX_OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY"):
            return "openrouter"
        return "openai"

    def _resolve_model(self, provider: str) -> str:
        stored = self._store_get("llm_model") or ""
        if stored:
            return stored
        env = os.getenv("QUANTX_LLM_MODEL")
        if env:
            return env
        if provider == "groq":
            return "llama-3.3-70b-versatile"
        if provider == "openrouter":
            return "openai/gpt-4o-mini"
        return "gpt-4o-mini"

    def _endpoint(self, provider: str) -> str:
        if provider == "groq":
            return "https://api.groq.com/openai/v1/chat/completions"
        if provider == "openrouter":
            return "https://openrouter.ai/api/v1/chat/completions"
        return "https://api.openai.com/v1/chat/completions"

    # —— public ask ——
    def ask(self, message: str, history: Optional[list[dict]] = None, intent: str = "chat") -> dict:
        text = (message or "").strip()
        if not text:
            return self._pack(
                "Ask me to review losses, missed strategy signals, or improve logic. "
                "Example: “Why did we lose on TCS?” or “What did the strategy miss today?”"
            )

        ctx = self._get_context()
        analysis_ctx = self._build_analysis_context(ctx, intent=intent, question=text)

        api_key = self._resolve_api_key()
        if not api_key:
            return self._pack(
                self._no_key_message() + "\n\n---\n" + self._fallback_loss_brief(analysis_ctx),
                mode="fallback_rules",
                analysis=analysis_ctx.get("loss_review"),
            )

        try:
            content = self._call_llm(text, analysis_ctx, history or [], intent=intent)
            return self._pack(content, mode="llm", analysis=analysis_ctx.get("loss_review"))
        except Exception as e:
            logger.exception("LLM chat failed")
            return self._pack(
                f"LLM call failed: {e}\n\nFalling back to structured loss brief:\n\n"
                + self._fallback_loss_brief(analysis_ctx),
                mode="fallback_rules",
                error=str(e),
                analysis=analysis_ctx.get("loss_review"),
            )

    def review_losses(self, history: Optional[list[dict]] = None) -> dict:
        prompt = (
            "Perform a full post-mortem on all losing trades and recent rejected signals. "
            "Explain root causes, what strategy/logic missed, and prioritized fixes. "
            "Be blunt and specific using the context."
        )
        return self.ask(prompt, history=history, intent="loss_review")

    # —— context assembly ——
    def _build_analysis_context(self, ctx: dict, intent: str, question: str) -> dict:
        positions = ctx.get("positions") or []
        closed = [p for p in positions if p.get("status") == "CLOSED"]
        opens = [p for p in positions if p.get("status") == "OPEN"]
        losses = [p for p in closed if float(p.get("pnl") or 0) <= 0]
        wins = [p for p in closed if float(p.get("pnl") or 0) > 0]
        journal = ctx.get("journal") or []
        cycles = ctx.get("cycles") or []
        rejected = []
        for c in cycles[:15]:
            for r in c.get("rejected") or []:
                rejected.append(
                    {
                        "cycle": c.get("cycle_no"),
                        "symbol": r.get("symbol"),
                        "reason": r.get("reason"),
                        "when": c.get("created_at"),
                    }
                )

        loss_review = {
            "losing_trades": [
                {
                    "symbol": p.get("symbol"),
                    "side": p.get("side"),
                    "qty": p.get("quantity"),
                    "entry": p.get("entry_price"),
                    "exit": p.get("exit_price"),
                    "pnl": p.get("pnl"),
                    "pnl_pct": p.get("pnl_pct"),
                    "stop_loss": p.get("stop_loss"),
                    "target_1": p.get("target_1"),
                    "exit_reason": p.get("exit_reason"),
                    "reason_entry": (p.get("reason") or "")[:400],
                    "opened_at": p.get("opened_at"),
                    "closed_at": p.get("closed_at"),
                }
                for p in losses[:20]
            ],
            "winning_trades_sample": [
                {
                    "symbol": p.get("symbol"),
                    "side": p.get("side"),
                    "pnl": p.get("pnl"),
                    "exit_reason": p.get("exit_reason"),
                }
                for p in wins[:10]
            ],
            "open_positions": [
                {
                    "symbol": p.get("symbol"),
                    "side": p.get("side"),
                    "entry": p.get("entry_price"),
                    "ltp": p.get("current_price"),
                    "pnl": p.get("pnl"),
                    "stop_loss": p.get("stop_loss"),
                    "target_1": p.get("target_1"),
                }
                for p in opens[:10]
            ],
            "recent_rejections": rejected[:25],
            "journal_lessons": [
                {
                    "symbol": j.get("symbol"),
                    "pnl": j.get("pnl"),
                    "mistakes": j.get("mistakes"),
                    "lessons": j.get("lessons"),
                    "market_condition": j.get("market_condition"),
                }
                for j in journal[:15]
                if float(j.get("pnl") or 0) <= 0 or j.get("mistakes")
            ],
            "stats": {
                "closed": len(closed),
                "losses": len(losses),
                "wins": len(wins),
                "total_loss_pnl": round(sum(float(p.get("pnl") or 0) for p in losses), 2),
                "total_win_pnl": round(sum(float(p.get("pnl") or 0) for p in wins), 2),
            },
            "strategy_catalog": [
                "swing_trend: EMA stack + SuperTrend + ADX continuation",
                "breakout: 20d range break + volume expansion",
                "intraday_mean_reversion: RSI/BB fade when ADX weak",
            ],
            "known_logic_gaps_to_consider": [
                "Entered without ADX strength confirmation",
                "Ignored elevated India VIX / macro risk-off",
                "Stop too tight vs ATR → noise stop-out",
                "No volume confirmation on breakout",
                "Held into opposite EMA stack",
                "Overtrading after consecutive losses",
                "Duplicate/no-slot rejects while chasing same names",
                "Slippage/fees eroded thin RR setups",
            ],
        }

        slim = {
            "intent": intent,
            "question": question,
            "portfolio": ctx.get("portfolio"),
            "risk": ctx.get("risk"),
            "paper": ctx.get("paper"),
            "macro": ctx.get("macro"),
            "performance": ctx.get("performance"),
            "pnl_summary": (ctx.get("pnl") or {}).get("summary"),
            "loss_review": loss_review,
            "recent_orders": (ctx.get("orders") or [])[:20],
            "recent_cycles": [
                {
                    "cycle_no": c.get("cycle_no"),
                    "message": c.get("message"),
                    "valid_count": c.get("valid_count"),
                    "executed_count": c.get("executed_count"),
                    "rejected_count": c.get("rejected_count"),
                    "executed": c.get("executed"),
                    "rejected": c.get("rejected"),
                }
                for c in (ctx.get("cycles") or [])[:8]
            ],
            "config_risk": (ctx.get("config") or {}).get("risk"),
        }
        return slim

    def _call_llm(self, message: str, analysis_ctx: dict, history: list[dict], intent: str) -> str:
        provider = self._resolve_provider()
        model = self._resolve_model(provider)
        api_key = self._resolve_api_key()
        url = self._endpoint(provider)

        intent_note = ""
        if intent == "loss_review":
            intent_note = (
                "\nUser requested a dedicated LOSS REVIEW. Focus on losing trades, "
                "missed filters, and strategy/logic gaps with prioritized fixes.\n"
            )

        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT
                + intent_note
                + "\n\nLIVE_TRADING_CONTEXT_JSON:\n"
                + json.dumps(analysis_ctx, default=str)[:14000],
            }
        ]
        for h in history[-10:]:
            if h.get("role") in ("user", "assistant") and h.get("content"):
                messages.append({"role": h["role"], "content": str(h["content"])[:4000]})
        messages.append({"role": "user", "content": message})

        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.25,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        if provider == "openrouter":
            headers["HTTP-Referer"] = "https://quantx.local"
            headers["X-Title"] = "QuantX Trading Agent"

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="ignore")
            raise LLMProviderError(f"{provider} HTTP {e.code}: {body[:500]}") from e
        except urllib.error.URLError as e:
            raise LLMProviderError(f"{provider} connection error: {e}") from e

        try:
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            raise LLMProviderError(f"Unexpected LLM response: {data}") from e

    def _pack(
        self,
        content: str,
        mode: str = "llm",
        error: Optional[str] = None,
        analysis: Any = None,
    ) -> dict:
        cfg = self.get_llm_config()
        return {
            "role": "assistant",
            "content": content,
            "timestamp": _now(),
            "mode": mode,
            "provider": cfg.get("provider"),
            "model": cfg.get("model") if mode == "llm" else None,
            "error": error,
            "analysis_stats": (analysis or {}).get("stats") if isinstance(analysis, dict) else None,
            "sources": ["portfolio", "positions", "orders", "cycles", "journal", "risk", "macro"],
        }

    def _no_key_message(self) -> str:
        return (
            "**LLM chat is not configured yet.**\n\n"
            "To enable strategy/loss reasoning with a real LLM:\n"
            "1. Open **Chat → LLM settings**\n"
            "2. Choose provider (**Groq** recommended / free tier, or OpenAI / OpenRouter)\n"
            "3. Paste your API key and Save\n\n"
            "Then ask: *“Why did we lose?”* or click **Analyze losses**.\n"
        )

    def _fallback_loss_brief(self, analysis_ctx: dict) -> str:
        lr = analysis_ctx.get("loss_review") or {}
        stats = lr.get("stats") or {}
        lines = [
            "**Structured loss brief (rules engine — enable LLM for deeper reasoning)**",
            f"Closed trades: {stats.get('closed', 0)} | Wins: {stats.get('wins', 0)} | "
            f"Losses: {stats.get('losses', 0)}",
            f"Gross win PnL: ₹{stats.get('total_win_pnl', 0):,.2f} | "
            f"Gross loss PnL: ₹{stats.get('total_loss_pnl', 0):,.2f}",
            "",
            "Losing trades:",
        ]
        losses = lr.get("losing_trades") or []
        if not losses:
            lines.append("• None in current book.")
        for p in losses[:10]:
            lines.append(
                f"• {p.get('symbol')} {p.get('side')} entry {p.get('entry')} → exit {p.get('exit')} "
                f"PnL ₹{float(p.get('pnl') or 0):,.2f} | {p.get('exit_reason')} | "
                f"SL {p.get('stop_loss')} T1 {p.get('target_1')}"
            )
        lines.append("\nRecent rejects:")
        rejs = lr.get("recent_rejections") or []
        if not rejs:
            lines.append("• None logged.")
        for r in rejs[:8]:
            lines.append(f"• Cycle #{r.get('cycle')} {r.get('symbol')}: {r.get('reason')}")
        lines.append(
            "\nLikely logic gaps to inspect once LLM is on: ADX/volume filters, VIX regime, "
            "ATR stop distance, consecutive-loss halt adherence, fee drag on thin RR."
        )
        return "\n".join(lines)


# Back-compat alias used by API
QuantXChat = QuantXLLMChat

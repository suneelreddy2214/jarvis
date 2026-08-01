"""QuantX FastAPI — paper trading agent API."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from quantx import __version__
from quantx.analysis.backtest import Backtester
from quantx.analysis.fno import DEFAULT_FO_UNIVERSE
from quantx.analysis.option_chain import FnoSearchService, SEARCH_UNIVERSE
from quantx.analysis.strategies import list_strategies
import quantx.analysis.additional_strategies  # noqa: F401 — register additive strategies
import quantx.analysis.trading_styles  # noqa: F401 — register style strategies
from quantx.analysis.learning import StrategyLearner
from quantx.analysis.trading_styles import get_trading_styles, train_trading_styles
from quantx.analysis.regime import RegimeDetector
from quantx.core.chat import QuantXChat
from quantx.core.config import get_settings
from quantx.core.emergency import EmergencyController
from quantx.core.engine import QuantXEngine
from quantx.core.market_hours import MarketClock
from quantx.core.models import TradeType
from quantx.data.market_data import DEFAULT_WATCHLIST, MarketDataService
from quantx.execution.base import PaperBrokerAdapter, broker_credentials_present
from quantx.execution.broker import PaperBroker
from quantx.execution.paper_agent import DEFAULT_PAPER_UNIVERSE, get_paper_agent
from quantx.portfolio.db import Database
from quantx.portfolio.manager import PortfolioManager
from quantx.reports.generator import ReportGenerator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("quantx.api")

settings = get_settings()
db = Database()
market_data = MarketDataService(use_live=True)
portfolio = PortfolioManager(db=db, market_data=market_data, settings=settings)
engine = QuantXEngine(settings=settings, risk_manager=portfolio.risk, market_data=market_data)
broker = PaperBroker(portfolio=portfolio, db=db, settings=settings)
emergency = EmergencyController(portfolio=portfolio, risk=portfolio.risk, db=db)
reports = ReportGenerator(portfolio=portfolio, engine=engine, data=market_data)
clock = MarketClock(settings)
paper_agent = get_paper_agent(portfolio=portfolio, engine=engine, broker=broker, settings=settings)
paper_adapter = PaperBrokerAdapter(broker)
backtester = Backtester(settings=settings, market_data=market_data)
fno_search = FnoSearchService(market_data=market_data)


def _chat_context() -> dict:
    snap = portfolio.snapshot()
    risk = portfolio.risk.status()
    opens = [p.model_dump(mode="json") for p in db.list_positions("OPEN")]
    closed = [p.model_dump(mode="json") for p in db.list_positions("CLOSED")]
    paper = paper_agent.status().get("session", {})
    perf = portfolio.performance()
    return {
        "portfolio": snap.model_dump(),
        "risk": risk.model_dump(),
        "margin": portfolio.margin_book(),
        "positions": opens + closed,
        "open_positions": opens,
        "orders": db.list_orders(100),
        "cycles": paper_agent.list_cycles(20),
        "journal": [j.model_dump(mode="json") for j in db.list_journal(50)],
        "performance": perf,
        "pnl": {
            "summary": {
                "capital": snap.capital,
                "total_pnl": snap.total_pnl,
                "unrealized_pnl": snap.unrealized_pnl,
                "closed_realized_pnl": sum(float(p.get("pnl") or 0) for p in closed),
                "total_fees": perf.get("total_fees", 0),
                "win_rate": perf.get("win_rate", 0),
                "wins": perf.get("wins", 0),
                "losses": perf.get("losses", 0),
                "open_positions": snap.open_positions,
                "closed_trades": len(closed),
            }
        },
        "paper": paper,
        "emergency": emergency.state().model_dump(),
        "macro": market_data.get_macro_snapshot(),
        "config": {"risk": settings.risk.model_dump()},
        "watchlist_symbols": list(DEFAULT_PAPER_UNIVERSE),
    }


def _llm_settings_store(op: str, key: str, default=None):
    if op == "get":
        return db.get_state(key, default)
    if op == "set":
        db.set_state(key, default if key == "value_placeholder" else default)
        # signature: store("set", key, value) — fix below
        return None
    return default


def _llm_store(op: str, key: str, value=None):
    if op == "get":
        return db.get_state(key, value)
    if op == "set":
        db.set_state(key, value)
        return True
    return None


chat_assistant = QuantXChat(_chat_context, settings_store=_llm_store)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("QuantX %s starting in %s mode", __version__, settings.agent.mode)
    yield
    if paper_agent.stats.running:
        paper_agent.stop()
    logger.info("QuantX shutting down")


app = FastAPI(
    title="QuantX",
    description="Institutional paper trading agent — capital preservation first.",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    symbol: str
    exchange: str = "NSE"
    trade_type: TradeType = TradeType.SWING
    strategy: Optional[str] = None


class ScanRequest(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: list(DEFAULT_PAPER_UNIVERSE)[:8])
    exchange: str = "NSE"
    trade_type: TradeType = TradeType.SWING
    strategy: Optional[str] = None
    enable_fno: bool = False
    fo_symbols: Optional[list[str]] = None


class ExecuteRequest(BaseModel):
    symbol: str
    exchange: str = "NSE"
    trade_type: TradeType = TradeType.SWING
    strategy: Optional[str] = None


class CloseRequest(BaseModel):
    position_id: int
    exit_price: Optional[float] = None
    reason: str = "Manual close"


class KillSwitchRequest(BaseModel):
    active: bool
    reason: str = "Operator kill switch"


class OverrideRequest(BaseModel):
    enabled: bool


class PaperStartRequest(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: list(DEFAULT_PAPER_UNIVERSE))
    interval_sec: int = 60
    auto_execute: bool = True
    trade_type: TradeType = TradeType.SWING
    enable_fno: bool = True
    trade_types: Optional[list[TradeType]] = None
    fo_symbols: Optional[list[str]] = None


class PaperResetRequest(BaseModel):
    confirm: bool = False


class PaperCapitalRequest(BaseModel):
    delta: Optional[float] = None
    set_to: Optional[float] = None
    reason: str = "Dashboard capital adjust"


class BacktestRequest(BaseModel):
    symbol: str
    strategy: str = "swing_trend"
    exchange: str = "NSE"
    period: str = "1y"
    capital: Optional[float] = None


class StyleTrainRequest(BaseModel):
    symbols: Optional[list[str]] = None
    period: str = "6mo"
    style_ids: Optional[list[str]] = None
    max_symbols_per_strategy: int = 2


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = Field(default_factory=list)
    intent: str = "chat"  # chat | loss_review


class LLMConfigRequest(BaseModel):
    api_key: str = ""
    provider: str = "groq"  # openai | groq | openrouter
    model: str = ""


@app.get("/api/health")
def health():
    return {
        "agent": "QuantX",
        "version": __version__,
        "mode": settings.agent.mode,
        "status": "ok",
        "paper_session": paper_agent.stats.running,
        "mission": "Protect capital first. Generate consistent profits second.",
    }


@app.get("/api/session")
def session():
    return clock.session().model_dump()


@app.get("/api/portfolio")
def get_portfolio():
    return portfolio.snapshot().model_dump()


@app.get("/api/margin")
def get_margin():
    """Stocks, F&O, and margin amount details for the dashboard."""
    return portfolio.margin_book()


@app.get("/api/performance")
def get_performance():
    return portfolio.performance()


@app.get("/api/pnl")
def get_pnl():
    """Full P&L breakdown: summary + open (unrealized) + closed (realized) + order fills."""
    # Refresh open MTM first so unrealized is current
    try:
        portfolio.mark_to_market()
    except Exception:
        logger.exception("MTM during /api/pnl failed")

    snap = portfolio.snapshot()
    perf = portfolio.performance()
    opens = [p.model_dump(mode="json") for p in db.list_positions("OPEN")]
    closed = [p.model_dump(mode="json") for p in db.list_positions("CLOSED")]
    journal = [j.model_dump(mode="json") for j in db.list_journal(200)]
    orders = db.list_orders(200)

    # Enrich FILLED orders with linked position PnL when possible
    pos_by_sym = {}
    for p in opens + closed:
        pos_by_sym.setdefault(p["symbol"], []).append(p)

    order_rows = []
    for o in orders:
        linked = None
        cands = pos_by_sym.get(o["symbol"] or "", [])
        # Prefer matching side + similar entry price
        for p in cands:
            if p.get("side") == o.get("side") and abs(float(p.get("entry_price") or 0) - float(o.get("price") or 0)) < 1.0:
                linked = p
                break
        if linked is None and cands:
            linked = cands[0]

        unrealized = None
        realized = None
        status_pos = None
        entry = o.get("price")
        exit_px = None
        if linked:
            status_pos = linked.get("status")
            if linked.get("status") == "OPEN":
                unrealized = linked.get("pnl")
            else:
                realized = linked.get("pnl")
                exit_px = linked.get("exit_price")

        order_rows.append(
            {
                **o,
                "position_status": status_pos,
                "entry_price": entry,
                "exit_price": exit_px,
                "unrealized_pnl": unrealized,
                "realized_pnl": realized,
                "pnl": realized if realized is not None else unrealized,
                "pnl_pct": linked.get("pnl_pct") if linked else None,
                "stop_loss": linked.get("stop_loss") if linked else None,
                "target_1": linked.get("target_1") if linked else None,
                "target_2": linked.get("target_2") if linked else None,
                "exit_reason": linked.get("exit_reason") if linked else None,
            }
        )

    open_unrealized = sum(float(p.get("pnl") or 0) for p in opens)
    closed_realized = sum(float(p.get("pnl") or 0) for p in closed)
    wins = [p for p in closed if float(p.get("pnl") or 0) > 0]
    losses = [p for p in closed if float(p.get("pnl") or 0) <= 0]

    return {
        "summary": {
            "capital": snap.capital,
            "initial_capital": settings.capital.initial,
            "total_pnl": snap.total_pnl,
            "realized_pnl_today": snap.realized_pnl_today,
            "realized_pnl_week": snap.realized_pnl_week,
            "unrealized_pnl": round(open_unrealized, 2),
            "closed_realized_pnl": round(closed_realized, 2),
            "total_fees": perf.get("total_fees", 0),
            "drawdown_pct": snap.drawdown_pct,
            "open_positions": len(opens),
            "closed_trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(closed) * 100, 2) if closed else 0.0,
            "mode": snap.mode,
        },
        "open_positions": opens,
        "closed_positions": closed,
        "journal": journal,
        "orders": order_rows,
    }


@app.get("/api/risk")
def get_risk():
    portfolio._hydrate_risk_from_db()
    return portfolio.risk.status().model_dump()


@app.get("/api/positions")
def get_positions(status: str = Query("OPEN")):
    if status.upper() == "ALL":
        return [p.model_dump(mode="json") for p in db.list_positions(None)]
    return [p.model_dump(mode="json") for p in db.list_positions(status.upper())]


@app.post("/api/positions/mark")
def mark_positions():
    positions = portfolio.mark_to_market()
    return {
        "positions": [p.model_dump(mode="json") for p in positions],
        "portfolio": portfolio.snapshot().model_dump(),
    }


@app.post("/api/positions/close")
def close_position(req: CloseRequest):
    pos = db.get_position(req.position_id)
    if not pos:
        raise HTTPException(404, "Position not found")
    price = req.exit_price if req.exit_price is not None else pos.current_price
    try:
        closed = portfolio.close_position(req.position_id, price, req.reason)
        return closed.model_dump(mode="json")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    strat = req.strategy
    if not strat:
        if req.trade_type == TradeType.FUTURES:
            strat = "futures_trend"
        elif req.trade_type == TradeType.OPTIONS:
            strat = "options_directional"
    rec = engine.analyze_symbol(req.symbol, req.exchange, req.trade_type, strategy=strat)
    return rec.model_dump(mode="json")


@app.post("/api/scan")
def scan(req: ScanRequest):
    results = []
    if req.enable_fno:
        # Stocks + Futures + Options in one pass
        equity = engine.scan_watchlist(req.symbols, req.exchange, TradeType.SWING, strategy=req.strategy)
        fo_syms = req.fo_symbols or list(DEFAULT_FO_UNIVERSE)
        futs = engine.scan_watchlist(fo_syms, req.exchange, TradeType.FUTURES, strategy="futures_trend")
        opts = engine.scan_watchlist(fo_syms, req.exchange, TradeType.OPTIONS, strategy="options_directional")
        results = equity + futs + opts
        results.sort(key=lambda r: (not r.valid, -r.scores.confidence))
    else:
        strat = req.strategy
        if not strat:
            if req.trade_type == TradeType.FUTURES:
                strat = "futures_trend"
            elif req.trade_type == TradeType.OPTIONS:
                strat = "options_directional"
        results = engine.scan_watchlist(req.symbols, req.exchange, req.trade_type, strategy=strat)
    return {
        "count": len(results),
        "valid_count": sum(1 for r in results if r.valid),
        "enable_fno": req.enable_fno,
        "recommendations": [r.model_dump(mode="json") for r in results],
    }


@app.post("/api/execute")
def execute(req: ExecuteRequest):
    strat = req.strategy
    if not strat:
        if req.trade_type == TradeType.FUTURES:
            strat = "futures_trend"
        elif req.trade_type == TradeType.OPTIONS:
            strat = "options_directional"
    rec = engine.analyze_symbol(req.symbol, req.exchange, req.trade_type, strategy=strat)
    result = broker.retry_safe(rec)
    result["recommendation"] = rec.model_dump(mode="json")
    return result


@app.get("/api/strategies")
def strategies():
    styles = get_trading_styles()
    return {
        "count": len(list_strategies()),
        "strategies": list_strategies(),
        "styles_count": len(styles),
        "styles": styles,
        "learning": StrategyLearner(db).as_dict(),
        "note": "Core strategies preserved; additive + trading-style strategies registered. Self-train via POST /api/styles/train.",
    }


@app.get("/api/styles")
def trading_styles():
    styles = get_trading_styles()
    return {
        "count": len(styles),
        "styles": styles,
        "strategy_count": len(list_strategies()),
        "note": "Trading styles mapped to QuantX strategies for AI paper self-training.",
    }


@app.post("/api/styles/train")
def train_styles(req: StyleTrainRequest):
    try:
        return train_trading_styles(
            backtester=backtester,
            learner=StrategyLearner(db),
            symbols=req.symbols,
            period=req.period,
            style_ids=req.style_ids,
            max_symbols_per_strategy=req.max_symbols_per_strategy,
        )
    except Exception as e:
        logger.exception("Style training failed")
        raise HTTPException(500, str(e))


@app.get("/api/regime")
def get_regime():
    macro_snap = market_data.get_macro_snapshot()
    macro = engine.macro.analyze(
        nifty_change_pct=macro_snap.get("nifty_change_pct"),
        india_vix=macro_snap.get("india_vix"),
        usdinr_change_pct=macro_snap.get("usdinr_change_pct"),
    )
    snap = None
    try:
        snap = engine.ta.analyze(market_data.get_ohlc("NIFTY", "NSE"))
    except Exception:
        snap = None
    regime = RegimeDetector().detect(
        snap=snap,
        india_vix=macro_snap.get("india_vix"),
        avoid_new_risk=macro.avoid_new_risk,
    )
    return {
        "regime": regime.as_dict(),
        "macro": {
            "summary": macro.summary,
            "avoid_new_risk": macro.avoid_new_risk,
            "india_vix": macro_snap.get("india_vix"),
        },
    }


@app.get("/api/learning")
def get_learning():
    learner = StrategyLearner(db)
    # Soft sync from journal so UI has data even before new closes
    try:
        learner.sync_from_journal(db.list_journal(500))
    except Exception:
        pass
    return learner.as_dict()


@app.post("/api/backtest")
def backtest(req: BacktestRequest):
    try:
        result = backtester.run(
            symbol=req.symbol,
            strategy=req.strategy,
            exchange=req.exchange,
            period=req.period,
            capital=req.capital,
        )
        return result.as_dict()
    except KeyError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("Backtest failed")
        raise HTTPException(500, str(e))


@app.get("/api/broker/status")
def broker_status():
    status = paper_adapter.status()
    status["agent_mode"] = settings.agent.mode
    status["live_ready"] = all(broker_credentials_present().values())
    status["note"] = (
        "Paper fills via QuantX PaperBroker + yfinance data. "
        "Set QUANTX_BROKER_* env vars and mode=live to enable Zerodha Kite."
    )
    return status


@app.post("/api/chat")
def chat(req: ChatRequest):
    if not req.message or not req.message.strip():
        raise HTTPException(400, "message is required")
    try:
        return chat_assistant.ask(req.message.strip(), history=req.history, intent=req.intent or "chat")
    except Exception as e:
        logger.exception("Chat failed")
        raise HTTPException(500, str(e))


@app.post("/api/chat/review-losses")
def chat_review_losses(req: ChatRequest = ChatRequest(message="review losses", intent="loss_review")):
    try:
        return chat_assistant.review_losses(history=req.history)
    except Exception as e:
        logger.exception("Loss review failed")
        raise HTTPException(500, str(e))


@app.get("/api/chat/llm-config")
def get_llm_config():
    return chat_assistant.get_llm_config()


@app.post("/api/chat/llm-config")
def set_llm_config(req: LLMConfigRequest):
    try:
        return chat_assistant.set_api_key(req.api_key, provider=req.provider, model=req.model)
    except Exception as e:
        raise HTTPException(400, str(e))


# —— Paper trading session ——
@app.get("/api/paper/status")
def paper_status():
    return paper_agent.status()


@app.post("/api/paper/start")
def paper_start(req: PaperStartRequest):
    if settings.agent.mode != "paper":
        raise HTTPException(400, "Agent mode is not paper")
    return paper_agent.start(
        symbols=req.symbols,
        interval_sec=req.interval_sec,
        auto_execute=req.auto_execute,
        trade_type=req.trade_type,
        enable_fno=req.enable_fno,
        trade_types=req.trade_types,
        fo_symbols=req.fo_symbols,
    )


@app.post("/api/paper/stop")
def paper_stop():
    return paper_agent.stop()


@app.post("/api/paper/cycle")
def paper_cycle():
    """Run one paper scan/execute/MTM cycle immediately."""
    return paper_agent.run_once()


@app.post("/api/paper/reset")
def paper_reset(req: PaperResetRequest):
    result = paper_agent.reset_account(confirm=req.confirm)
    if not result.get("ok"):
        raise HTTPException(400, result.get("message", "Reset refused"))
    return result


@app.post("/api/paper/capital")
def paper_capital(req: PaperCapitalRequest):
    """Increase / decrease / set paper capital without wiping trade history."""
    if req.delta is None and req.set_to is None:
        raise HTTPException(400, "Provide delta or set_to")
    if req.delta is not None and req.set_to is not None:
        raise HTTPException(400, "Provide only one of delta or set_to")
    try:
        return portfolio.adjust_capital(
            delta=float(req.delta or 0),
            set_to=req.set_to,
            reason=req.reason,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/journal")
def journal(limit: int = 50):
    return [j.model_dump(mode="json") for j in db.list_journal(limit)]


@app.get("/api/orders")
def get_orders(limit: int = 100):
    return db.list_orders(limit)


@app.get("/api/paper/cycles")
def paper_cycles(limit: int = 50):
    return paper_agent.list_cycles(limit)


@app.get("/api/quote/{symbol}")
def quote(symbol: str, exchange: str = "NSE"):
    return market_data.get_quote(symbol, exchange)


@app.get("/api/search")
def search_symbols(q: str = Query("", description="Symbol search text"), limit: int = Query(20, ge=1, le=50)):
    """Search stocks / indices for the Search tab."""
    return {"query": q, "results": fno_search.search(q, limit=limit), "universe_size": len(SEARCH_UNIVERSE)}


@app.get("/api/fno/{symbol}")
def fno_overview(symbol: str):
    """Spot, futures basis, and available option expiry dates."""
    try:
        return fno_search.overview(symbol)
    except Exception as e:
        raise HTTPException(404, f"Symbol unavailable: {e}")


@app.get("/api/fno/{symbol}/expiries")
def fno_expiries(symbol: str):
    try:
        ov = fno_search.overview(symbol)
        return {"symbol": ov["symbol"], "spot": ov["spot"], "expiries": ov["expiries"]}
    except Exception as e:
        raise HTTPException(404, str(e))


@app.get("/api/fno/{symbol}/chain")
def fno_chain(symbol: str, expiry: Optional[str] = Query(None, description="YYYY-MM-DD expiry")):
    """Call/Put option chain for selected expiry."""
    try:
        return fno_search.chain(symbol, expiry=expiry)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("Option chain failed for %s", symbol)
        raise HTTPException(500, str(e))


@app.get("/api/macro")
def macro():
    return market_data.get_macro_snapshot()


@app.get("/api/watchlist")
def watchlist():
    quotes = []
    for sym in DEFAULT_WATCHLIST:
        try:
            quotes.append(
                market_data.get_quote(sym.replace(".NS", "").replace(".BO", ""), "NSE")
            )
        except Exception:
            continue
    return quotes


@app.get("/api/emergency")
def get_emergency():
    return emergency.state().model_dump()


@app.post("/api/emergency/kill-switch")
def kill_switch(req: KillSwitchRequest):
    return emergency.kill_switch(req.active, req.reason).model_dump()


@app.post("/api/emergency/panic-exit")
def panic_exit():
    return emergency.panic_exit()


@app.post("/api/emergency/override")
def override(req: OverrideRequest):
    return emergency.set_manual_override(req.enabled).model_dump()


@app.post("/api/emergency/clear-drawdown-lock")
def clear_dd():
    return emergency.clear_drawdown_lock().model_dump()


@app.get("/api/reports/{report_type}")
def get_report(report_type: str):
    mapping = {
        "morning": reports.morning_report,
        "open": reports.market_open_report,
        "intraday": reports.intraday_report,
        "closing": reports.closing_report,
        "weekly": reports.weekly_review,
        "risk": reports.risk_analysis,
    }
    fn = mapping.get(report_type)
    if not fn:
        raise HTTPException(404, f"Unknown report type. Choose from: {list(mapping)}")
    return fn().model_dump(mode="json")


@app.get("/api/config")
def get_config():
    return {
        "agent": settings.agent.model_dump(),
        "capital": settings.capital.model_dump(),
        "risk": settings.risk.model_dump(),
        "markets": settings.markets.model_dump(),
        "entry": settings.entry.model_dump(),
        "exit": settings.exit.model_dump(),
        "broker": settings.broker,
        "paper_universe": DEFAULT_PAPER_UNIVERSE,
    }

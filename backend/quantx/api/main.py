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
from quantx.analysis.strategies import list_strategies
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


class PaperResetRequest(BaseModel):
    confirm: bool = False


class BacktestRequest(BaseModel):
    symbol: str
    strategy: str = "swing_trend"
    exchange: str = "NSE"
    period: str = "1y"
    capital: Optional[float] = None


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


@app.get("/api/performance")
def get_performance():
    return portfolio.performance()


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
    rec = engine.analyze_symbol(req.symbol, req.exchange, req.trade_type, strategy=req.strategy)
    return rec.model_dump(mode="json")


@app.post("/api/scan")
def scan(req: ScanRequest):
    results = engine.scan_watchlist(req.symbols, req.exchange, req.trade_type, strategy=req.strategy)
    return {
        "count": len(results),
        "valid_count": sum(1 for r in results if r.valid),
        "recommendations": [r.model_dump(mode="json") for r in results],
    }


@app.post("/api/execute")
def execute(req: ExecuteRequest):
    rec = engine.analyze_symbol(req.symbol, req.exchange, req.trade_type, strategy=req.strategy)
    result = broker.retry_safe(rec)
    result["recommendation"] = rec.model_dump(mode="json")
    return result


@app.get("/api/strategies")
def strategies():
    return list_strategies()


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

"""QuantX FastAPI — paper trading agent API."""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from quantx import __version__
from quantx.analysis.backtest import Backtester
from quantx.analysis.fno import (
    DEFAULT_FO_UNIVERSE,
    encode_fo_meta,
    is_index,
    paper_option_levels,
    paper_option_premium,
)
from quantx.analysis.option_chain import FnoSearchService, SEARCH_UNIVERSE
from quantx.analysis.strategies import list_strategies
import quantx.analysis.additional_strategies  # noqa: F401 — register additive strategies
import quantx.analysis.trading_styles  # noqa: F401 — register style strategies
from quantx.analysis.learning import StrategyLearner
from quantx.analysis.trading_styles import (
    get_trading_styles,
    train_trading_styles,
    style_strategy_jobs,
    resolve_style_ids,
)
from quantx.analysis.opportunity_hunter import OpportunityHunter, HUNT_UNIVERSE
from quantx.analysis.regime import RegimeDetector
from quantx.core.chat import QuantXChat
from quantx.core.config import get_settings
from quantx.core.emergency import EmergencyController
from quantx.core.engine import QuantXEngine
from quantx.core.market_hours import MarketClock
from quantx.core.models import (
    AIScores,
    MarketDirection,
    Side,
    TradeRecommendation,
    TradeType,
)
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


def _build_market_data() -> MarketDataService:
    md = getattr(settings, "market_data", None)
    return MarketDataService(
        use_live=True,
        yahoo_only=bool(getattr(md, "yahoo_only", True)),
        allow_synthetic=bool(getattr(md, "allow_synthetic", False)),
        quote_cache_ttl_sec=float(getattr(md, "quote_cache_ttl_sec", 60) or 60),
        ohlc_cache_ttl_sec=float(getattr(md, "ohlc_cache_ttl_sec", 300) or 300),
    )


market_data = _build_market_data()
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


def _backend_style_self_train() -> None:
    """AI trading-style self-train — backend only (no UI trigger)."""
    try:
        logger.info("Backend trading-styles self-train starting…")
        result = train_trading_styles(
            backtester=backtester,
            learner=StrategyLearner(db),
            period="6mo",
            max_symbols_per_strategy=2,
        )
        logger.info(
            "Backend style self-train complete — %s strategies / %s backtests",
            len(result.get("strategies_trained") or []),
            result.get("backtests_run", 0),
        )
    except Exception:
        logger.exception("Backend style self-train failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("QuantX %s starting in %s mode", __version__, settings.agent.mode)
    threading.Thread(
        target=_backend_style_self_train,
        name="quantx-style-self-train",
        daemon=True,
    ).start()
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
    trade_types: Optional[list[str]] = None  # multi-select: SWING/INTRADAY/FUTURES/OPTIONS
    strategy: Optional[str] = None
    enable_fno: bool = False
    fo_symbols: Optional[list[str]] = None
    hunt_market: bool = False
    top_n: int = 8
    style_ids: Optional[list[str]] = None
    min_hunt_score: float = 52.0


class HuntRequest(BaseModel):
    seed_symbols: Optional[list[str]] = None
    top_n: int = 8
    exchange: str = "NSE"
    enable_fno: bool = True
    trade_type: str = "ALL"
    trade_types: Optional[list[str]] = None
    style_ids: Optional[list[str]] = None
    min_score: float = 52.0
    strategies_per_product: int = 2


class ExecuteRequest(BaseModel):
    symbol: str
    exchange: str = "NSE"
    trade_type: TradeType = TradeType.SWING
    strategy: Optional[str] = None
    # When set, execute this recommendation as-is (preserves SL/target from scan)
    side: Optional[str] = None
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    target_1: Optional[float] = None
    target_2: Optional[float] = None
    quantity: Optional[int] = None
    reason: Optional[str] = None
    market_direction: Optional[str] = None
    force: bool = False  # paper operator override — place even if gates rejected setup


class FnoPlaceRequest(BaseModel):
    """Manual F&O paper order with stop-loss / targets."""

    symbol: str  # underlying
    exchange: str = "NSE"
    product: str = "FUTURES"  # FUTURES | OPTIONS
    side: str = "BUY"
    quantity: int = 1  # lots
    option_type: Optional[str] = None  # CE | PE
    strike: Optional[float] = None
    expiry: Optional[str] = None
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    target_1: Optional[float] = None
    target_2: Optional[float] = None
    auto_levels: bool = True
    reason: str = "Manual F&O paper order"


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
    style_ids: Optional[list[str]] = None


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


def _normalize_trade_types(
    trade_types: Optional[list[str]],
    *,
    trade_type: str | TradeType = "SWING",
    enable_fno: bool = False,
) -> list[str]:
    """Resolve multi-select products; empty → derive from trade_type / F&O flag."""
    allowed = {"SWING", "INTRADAY", "FUTURES", "OPTIONS"}
    if trade_types:
        out: list[str] = []
        for t in trade_types:
            key = str(t).upper().strip()
            if key in ("STOCKS", "EQUITY", "CASH"):
                key = "SWING"
            if key in allowed and key not in out:
                out.append(key)
        if out:
            return out
    tt = trade_type.value if isinstance(trade_type, TradeType) else str(trade_type).upper()
    if enable_fno or tt == "ALL":
        return ["SWING", "INTRADAY", "FUTURES", "OPTIONS"]
    if tt in allowed:
        # Stocks-only still includes intraday equity by default
        return [tt] if tt != "SWING" else ["SWING", "INTRADAY"]
    return ["SWING", "INTRADAY"]


def _normalize_style_ids(style_ids: Optional[list[str]]) -> Optional[list[str]]:
    """Expand 'all' / aliases into concrete catalog ids so hunt never gets 0 strategies."""
    if style_ids is None:
        return None
    return [s.id for s in resolve_style_ids(style_ids)]


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
    products = _normalize_trade_types(
        req.trade_types,
        trade_type=req.trade_type,
        enable_fno=req.enable_fno,
    )
    enable_fno = req.enable_fno or any(t in ("FUTURES", "OPTIONS") for t in products)
    style_ids = _normalize_style_ids(req.style_ids)

    # AI market hunt: discover opportunistic symbols then process via strategies/styles
    if req.hunt_market:
        hunter = OpportunityHunter(engine=engine, market_data=market_data, db=db)
        hunt_styles = style_ids if style_ids is not None else [s.id for s in resolve_style_ids(None)]
        result = hunter.hunt_and_process(
            seed_symbols=req.symbols,
            top_n=req.top_n,
            exchange=req.exchange,
            enable_fno=enable_fno,
            trade_type="ALL" if set(products) >= {"SWING", "FUTURES", "OPTIONS"} else products[0],
            trade_types=products,
            style_ids=hunt_styles,
            min_score=req.min_hunt_score,
        )
        return result

    # Style-driven scan (Scalping → ETF investing, etc.)
    if style_ids is not None:
        jobs = style_strategy_jobs(style_ids, trade_type_filter=products, limit_per_style=4)
        # Hard fallback so UI never shows "0 strategies"
        if not jobs:
            for tt in products:
                fallback = {
                    "FUTURES": ("futures", "Futures Trading", "Intraday to weeks", "futures_trend"),
                    "OPTIONS": ("options", "Options Trading", "Intraday to expiry", "options_directional"),
                    "INTRADAY": ("intraday", "Intraday (Day Trading)", "Minutes to hours", "intraday_momentum"),
                    "SWING": ("swing", "Swing Trading", "2–30 days", "swing_trend"),
                }[tt]
                jobs.append(
                    {
                        "style_id": fallback[0],
                        "style_name": fallback[1],
                        "holding_period": fallback[2],
                        "strategy": fallback[3],
                        "trade_type": tt,
                    }
                )
        fo_syms = req.fo_symbols or list(DEFAULT_FO_UNIVERSE)
        equity_syms = [s for s in req.symbols if not is_index(s)] or list(req.symbols)
        results = []
        strategies_used: dict[str, list[str]] = {}
        styles_touched = []
        seen_styles: set[str] = set()
        for job in jobs:
            trade_type = TradeType(job["trade_type"])
            syms = fo_syms if trade_type in (TradeType.FUTURES, TradeType.OPTIONS) else equity_syms
            try:
                batch = engine.scan_watchlist(syms, req.exchange, trade_type, strategy=job["strategy"])
                for r in batch:
                    r.supporting_indicators = list(r.supporting_indicators or []) + [
                        f"style:{job['style_id']}",
                        f"holding:{job['holding_period']}",
                    ]
                    if r.reason and f"[{job['strategy']}]" not in r.reason:
                        r.reason = f"[{job['style_name']}] {r.reason}"
                results.extend(batch)
                strategies_used.setdefault(job["trade_type"], [])
                if job["strategy"] not in strategies_used[job["trade_type"]]:
                    strategies_used[job["trade_type"]].append(job["strategy"])
                if job["style_id"] not in seen_styles:
                    seen_styles.add(job["style_id"])
                    styles_touched.append(
                        {
                            "id": job["style_id"],
                            "name": job["style_name"],
                            "holding_period": job["holding_period"],
                        }
                    )
            except Exception as e:
                logger.exception("Style scan failed %s/%s: %s", job["style_id"], job["strategy"], e)

        best = {}
        for r in results:
            key = (r.symbol, r.trade_type.value)
            prev = best.get(key)
            if prev is None or (r.valid and not prev.valid) or (
                r.valid == prev.valid and r.scores.confidence > prev.scores.confidence
            ):
                best[key] = r
        results = list(best.values())
        results.sort(key=lambda r: (not r.valid, -r.scores.confidence))
        return {
            "count": len(results),
            "valid_count": sum(1 for r in results if r.valid),
            "enable_fno": enable_fno,
            "mode": "trading_styles",
            "trade_types": products,
            "hunted_symbols": req.symbols,
            "strategies_used": strategies_used,
            "styles_touched": styles_touched,
            "jobs_run": len(jobs),
            "recommendations": [r.model_dump(mode="json") for r in results],
            "message": (
                f"Scanned {len(styles_touched)} trading style(s) / {len(jobs)} strategy jobs "
                f"· products {','.join(products)}"
            ),
        }

    results = []
    fo_syms = req.fo_symbols or list(DEFAULT_FO_UNIVERSE)
    for tt in products:
        try:
            syms = fo_syms if tt in ("FUTURES", "OPTIONS") else req.symbols
            strat = req.strategy
            if not strat:
                strat = {
                    "FUTURES": "futures_trend",
                    "OPTIONS": "options_directional",
                    "INTRADAY": "intraday_momentum",
                    "SWING": "swing_trend",
                }.get(tt)
            batch = engine.scan_watchlist(syms, req.exchange, TradeType(tt), strategy=strat)
            results.extend(batch)
        except Exception as e:
            logger.exception("Product scan failed %s: %s", tt, e)
    best = {}
    for r in results:
        key = (r.symbol, r.trade_type.value)
        prev = best.get(key)
        if prev is None or (r.valid and not prev.valid) or (
            r.valid == prev.valid and r.scores.confidence > prev.scores.confidence
        ):
            best[key] = r
    results = list(best.values())
    results.sort(key=lambda r: (not r.valid, -r.scores.confidence))
    return {
        "count": len(results),
        "valid_count": sum(1 for r in results if r.valid),
        "enable_fno": enable_fno,
        "mode": "products",
        "trade_types": products,
        "hunted_symbols": req.symbols,
        "recommendations": [r.model_dump(mode="json") for r in results],
        "message": f"Scanned products {','.join(products)} · {sum(1 for r in results if r.valid)}/{len(results)} actionable",
    }


@app.post("/api/hunt")
def hunt_market(req: HuntRequest):
    """Go to market, hunt opportunistic symbols, process with strategies/trading styles."""
    hunter = OpportunityHunter(engine=engine, market_data=market_data, db=db)
    products = _normalize_trade_types(
        req.trade_types,
        trade_type=req.trade_type,
        enable_fno=req.enable_fno,
    )
    style_ids = _normalize_style_ids(req.style_ids)
    try:
        return hunter.hunt_and_process(
            seed_symbols=req.seed_symbols,
            top_n=req.top_n,
            exchange=req.exchange,
            enable_fno=req.enable_fno or any(t in ("FUTURES", "OPTIONS") for t in products),
            trade_type=req.trade_type,
            trade_types=products,
            style_ids=style_ids if style_ids is not None else [s.id for s in resolve_style_ids(None)],
            min_score=req.min_score,
            strategies_per_product=req.strategies_per_product,
        )
    except Exception as e:
        logger.exception("Market hunt failed")
        raise HTTPException(500, str(e))


@app.get("/api/hunt/universe")
def hunt_universe():
    return {
        "count": len(HUNT_UNIVERSE),
        "symbols": HUNT_UNIVERSE,
        "note": "AI hunt screens this universe for momentum, volume, breakout, oversold/overbought opportunities.",
    }


@app.post("/api/execute")
def execute(req: ExecuteRequest):
    """Execute paper order — prefers scanned recommendation levels (entry/SL/targets)."""
    if req.entry is not None and req.stop_loss is not None and req.target_1 is not None and req.side:
        try:
            side = Side(req.side.upper())
        except Exception as e:
            raise HTTPException(400, f"Invalid side: {e}")
        try:
            direction = MarketDirection(req.market_direction) if req.market_direction else MarketDirection.NEUTRAL
        except Exception:
            direction = MarketDirection.NEUTRAL
        qty = int(req.quantity or 0)
        if qty <= 0:
            raise HTTPException(400, "quantity must be > 0 when placing from recommendation")
        rec = TradeRecommendation(
            symbol=req.symbol.upper(),
            exchange=req.exchange,
            market_direction=direction,
            trade_type=req.trade_type,
            side=side,
            entry=float(req.entry),
            stop_loss=float(req.stop_loss),
            target_1=float(req.target_1),
            target_2=float(req.target_2 if req.target_2 is not None else req.target_1),
            risk_reward=round(abs(float(req.target_1) - float(req.entry)) / max(abs(float(req.entry) - float(req.stop_loss)), 1e-6), 2),
            quantity=qty,
            capital_at_risk=abs(float(req.entry) - float(req.stop_loss)) * qty,
            scores=AIScores(
                confidence=70 if req.force else 65,
                risk_score=40,
                volatility_score=50,
                probability_of_success=60,
            ),
            reason=req.reason or f"Dashboard execute {req.trade_type.value}",
            valid=True,
            rejection_reason=None,
        )
        if req.force:
            rec.supporting_indicators = ["operator_force"]
        result = broker.retry_safe(rec)
        result["recommendation"] = rec.model_dump(mode="json")
        return result

    strat = req.strategy
    if not strat:
        if req.trade_type == TradeType.FUTURES:
            strat = "futures_trend"
        elif req.trade_type == TradeType.OPTIONS:
            strat = "options_directional"
        elif req.trade_type == TradeType.INTRADAY:
            strat = "intraday_momentum"
    rec = engine.analyze_symbol(req.symbol, req.exchange, req.trade_type, strategy=strat)
    if req.force and not rec.valid and rec.quantity > 0 and rec.entry > 0:
        rec.valid = True
        rec.rejection_reason = None
        rec.reason = (rec.reason or "") + " [operator force]"
    result = broker.retry_safe(rec)
    result["recommendation"] = rec.model_dump(mode="json")
    return result


@app.post("/api/fno/place")
def fno_place(req: FnoPlaceRequest):
    """Place paper Futures/Options order with stop-loss and targets."""
    if settings.agent.mode != "paper":
        raise HTTPException(400, "F&O place is paper-only in this build")
    product = req.product.upper().strip()
    if product not in ("FUTURES", "OPTIONS"):
        raise HTTPException(400, "product must be FUTURES or OPTIONS")
    try:
        side = Side(req.side.upper())
    except Exception as e:
        raise HTTPException(400, f"Invalid side: {e}")
    if req.quantity <= 0:
        raise HTTPException(400, "quantity (lots) must be > 0")

    symbol = req.symbol.upper().replace(".NS", "").replace(".BO", "")
    try:
        quote = market_data.get_quote(symbol, req.exchange)
        spot = float(quote["price"])
    except Exception as e:
        raise HTTPException(400, f"Quote unavailable for {symbol}: {e}")

    atr = 0.0
    try:
        df = market_data.get_ohlc(symbol, req.exchange, period="3mo")
        snap = engine.ta.analyze(df)
        atr = float(snap.atr or 0)
        direction = snap.trend if snap.trend else MarketDirection.NEUTRAL
    except Exception:
        direction = MarketDirection.BULLISH if side == Side.BUY else MarketDirection.BEARISH

    entry = float(req.entry) if req.entry is not None else spot
    stop = req.stop_loss
    t1 = req.target_1
    t2 = req.target_2
    reason = req.reason
    fo_meta = ""

    if product == "OPTIONS":
        kind = (req.option_type or ("CE" if side == Side.BUY else "PE")).upper()
        if kind not in ("CE", "PE"):
            raise HTTPException(400, "option_type must be CE or PE")
        strike = float(req.strike) if req.strike is not None else round(spot / 50) * 50
        if req.entry is None:
            entry = paper_option_premium(spot, atr or spot * 0.01)
        if req.auto_levels and (stop is None or t1 is None):
            levels = paper_option_levels(entry)
            stop = stop if stop is not None else levels["stop_loss"]
            t1 = t1 if t1 is not None else levels["target_1"]
            t2 = t2 if t2 is not None else levels["target_2"]
        fo_meta = encode_fo_meta(spot, kind, strike, req.expiry)
        reason = f"{reason} {fo_meta} {kind} strike={strike}"
        order_side = Side.BUY  # paper long options only
        trade_type = TradeType.OPTIONS
    else:
        # Futures: SL/target from ATR
        if req.auto_levels and (stop is None or t1 is None):
            stop_dist = max(atr * 1.5, entry * 0.008, 1.0)
            if side == Side.BUY:
                stop = stop if stop is not None else round(entry - stop_dist, 2)
                t1 = t1 if t1 is not None else round(entry + 2 * stop_dist, 2)
                t2 = t2 if t2 is not None else round(entry + 3 * stop_dist, 2)
            else:
                stop = stop if stop is not None else round(entry + stop_dist, 2)
                t1 = t1 if t1 is not None else round(entry - 2 * stop_dist, 2)
                t2 = t2 if t2 is not None else round(entry - 3 * stop_dist, 2)
        if req.expiry:
            reason = f"{reason} [FUT expiry={req.expiry}]"
        order_side = side
        trade_type = TradeType.FUTURES

    if stop is None or t1 is None:
        raise HTTPException(400, "stop_loss and target_1 required (or set auto_levels=true)")
    stop = float(stop)
    t1 = float(t1)
    t2 = float(t2 if t2 is not None else t1)
    risk = abs(entry - stop)
    rr = round(abs(t1 - entry) / risk, 2) if risk > 0 else 0.0

    rec = TradeRecommendation(
        symbol=symbol,
        exchange=req.exchange,
        market_direction=direction,
        trade_type=trade_type,
        side=order_side,
        entry=round(entry, 2),
        stop_loss=round(stop, 2),
        target_1=round(t1, 2),
        target_2=round(t2, 2),
        risk_reward=rr,
        quantity=int(req.quantity),
        capital_at_risk=round(risk * req.quantity, 2),
        scores=AIScores(confidence=72, risk_score=45, volatility_score=55, probability_of_success=62),
        reason=reason,
        options_summary=fo_meta if product == "OPTIONS" else "",
        supporting_indicators=["manual_fno", f"product:{product}"],
        valid=True,
    )
    result = broker.retry_safe(rec)
    result["recommendation"] = rec.model_dump(mode="json")
    result["product"] = product
    result["levels"] = {
        "entry": rec.entry,
        "stop_loss": rec.stop_loss,
        "target_1": rec.target_1,
        "target_2": rec.target_2,
        "risk_reward": rec.risk_reward,
        "quantity_lots": rec.quantity,
    }
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
        "note": "Core strategies preserved; additive + trading-style strategies registered. Self-train runs on backend startup (POST /api/styles/train still available).",
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
        "Paper fills via QuantX PaperBroker using Yahoo Finance prices only "
        "(stocks + F&O underlyings). Synthetic prices disabled in paper mode. "
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
        style_ids=req.style_ids,
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
    try:
        q = market_data.get_quote(symbol, exchange)
        q["provider"] = "yahoo_finance"
        return q
    except Exception as e:
        raise HTTPException(502, f"Yahoo Finance quote failed: {e}")


@app.get("/api/market-data/status")
def market_data_status():
    """Confirm paper trading is wired to Yahoo Finance only."""
    sample = {}
    err = None
    try:
        sample = market_data.get_quote("RELIANCE", "NSE")
    except Exception as e:
        err = str(e)
    return {
        "ok": err is None,
        "config": getattr(settings, "market_data", None).model_dump()
        if getattr(settings, "market_data", None)
        else {"provider": "yahoo_finance", "yahoo_only": True},
        "runtime": market_data.provider_status(),
        "sample_quote": sample,
        "error": err,
        "note": "Paper stocks + F&O underlyings use Yahoo Finance only. Synthetic prices disabled.",
    }


@app.post("/api/market-data/clear-cache")
def market_data_clear_cache():
    market_data.clear_cache()
    return {"ok": True, "message": "Yahoo quote/OHLC cache cleared"}


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

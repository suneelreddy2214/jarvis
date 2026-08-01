#!/usr/bin/env python3
"""QuantX paper trading CLI.

Examples:
  python -m quantx.cli paper status
  python -m quantx.cli paper cycle
  python -m quantx.cli paper start --interval 120
  python -m quantx.cli paper stop
  python -m quantx.cli paper reset --confirm
  python -m quantx.cli analyze RELIANCE
  python -m quantx.cli scan RELIANCE,TCS,INFY
"""

from __future__ import annotations

import argparse
import json
import sys

from quantx.core.config import get_settings
from quantx.core.engine import QuantXEngine
from quantx.core.models import TradeType
from quantx.data.market_data import MarketDataService
from quantx.execution.broker import PaperBroker
from quantx.execution.paper_agent import DEFAULT_PAPER_UNIVERSE, PaperTradingAgent
from quantx.portfolio.db import Database
from quantx.portfolio.manager import PortfolioManager


def _build_agent(live: bool = True) -> PaperTradingAgent:
    settings = get_settings()
    db = Database()
    data = MarketDataService(use_live=live)
    portfolio = PortfolioManager(db=db, market_data=data, settings=settings)
    engine = QuantXEngine(settings=settings, risk_manager=portfolio.risk, market_data=data)
    broker = PaperBroker(portfolio=portfolio, db=db, settings=settings)
    return PaperTradingAgent(portfolio=portfolio, engine=engine, broker=broker, settings=settings)


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quantx", description="QuantX paper trading agent")
    sub = parser.add_subparsers(dest="cmd", required=True)

    paper = sub.add_parser("paper", help="Paper trading session controls")
    paper_sub = paper.add_subparsers(dest="paper_cmd", required=True)
    paper_sub.add_parser("status")
    start_p = paper_sub.add_parser("start")
    start_p.add_argument("--symbols", default=",".join(DEFAULT_PAPER_UNIVERSE))
    start_p.add_argument("--interval", type=int, default=60)
    start_p.add_argument("--no-auto", action="store_true")
    start_p.add_argument("--type", default="SWING", choices=["SWING", "INTRADAY"])
    paper_sub.add_parser("stop")
    paper_sub.add_parser("cycle")
    reset_p = paper_sub.add_parser("reset")
    reset_p.add_argument("--confirm", action="store_true")

    an = sub.add_parser("analyze", help="Analyze one symbol")
    an.add_argument("symbol")
    an.add_argument("--type", default="SWING", choices=["SWING", "INTRADAY"])

    sc = sub.add_parser("scan", help="Scan comma-separated symbols")
    sc.add_argument("symbols")
    sc.add_argument("--type", default="SWING", choices=["SWING", "INTRADAY"])

    ex = sub.add_parser("execute", help="Analyze + paper execute one symbol")
    ex.add_argument("symbol")
    ex.add_argument("--type", default="SWING", choices=["SWING", "INTRADAY"])

    sub.add_parser("portfolio", help="Show portfolio snapshot")
    sub.add_parser("performance", help="Show paper performance stats")

    args = parser.parse_args(argv)
    agent = _build_agent(live=True)

    if args.cmd == "paper":
        if args.paper_cmd == "status":
            _print(agent.status())
        elif args.paper_cmd == "start":
            symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
            _print(
                agent.start(
                    symbols=symbols,
                    interval_sec=args.interval,
                    auto_execute=not args.no_auto,
                    trade_type=TradeType(args.type),
                )
            )
            print("Session running in background thread of this process. Use Ctrl+C to keep alive.")
            try:
                while agent.stats.running:
                    import time

                    time.sleep(1)
            except KeyboardInterrupt:
                _print(agent.stop())
        elif args.paper_cmd == "stop":
            _print(agent.stop())
        elif args.paper_cmd == "cycle":
            _print(agent.run_once())
        elif args.paper_cmd == "reset":
            _print(agent.reset_account(confirm=args.confirm))
        return 0

    if args.cmd == "analyze":
        rec = agent.engine.analyze_symbol(args.symbol, trade_type=TradeType(args.type))
        _print(rec.model_dump(mode="json"))
        return 0

    if args.cmd == "scan":
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        recs = agent.engine.scan_watchlist(symbols, trade_type=TradeType(args.type))
        _print({
            "count": len(recs),
            "valid": sum(1 for r in recs if r.valid),
            "recommendations": [r.model_dump(mode="json") for r in recs],
        })
        return 0

    if args.cmd == "execute":
        rec = agent.engine.analyze_symbol(args.symbol, trade_type=TradeType(args.type))
        result = agent.broker.retry_safe(rec)
        result["recommendation"] = rec.model_dump(mode="json")
        _print(result)
        return 0

    if args.cmd == "portfolio":
        _print(agent.portfolio.snapshot().model_dump())
        return 0

    if args.cmd == "performance":
        _print(agent.portfolio.performance())
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())

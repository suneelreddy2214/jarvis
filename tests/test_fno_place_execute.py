"""F&O place + execute-from-recommendation levels."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def test_normalize_and_hunt_styles_not_empty():
    from quantx.api.main import _normalize_style_ids, _normalize_trade_types
    from quantx.analysis.opportunity_hunter import OpportunityHunter
    from quantx.core.config import Settings
    from quantx.core.engine import QuantXEngine
    from quantx.core.risk import RiskManager
    from quantx.data.market_data import MarketDataService
    from quantx.portfolio.db import Database

    assert _normalize_style_ids(["all"]) and len(_normalize_style_ids(["all"])) >= 20
    assert _normalize_trade_types(["SWING"], enable_fno=False) == ["SWING"]
    assert "INTRADAY" in _normalize_trade_types(None, trade_type="SWING", enable_fno=False)
    assert set(_normalize_trade_types(["SWING", "FUTURES", "OPTIONS"])) >= {"SWING", "FUTURES", "OPTIONS"}

    s = Settings()
    import tempfile

    td = tempfile.mkdtemp()
    s.database = {"path": str(Path(td) / "t.db")}
    s.agent.mode = "paper"
    db = Database(Path(s.database["path"]))
    data = MarketDataService(use_live=False)
    risk = RiskManager(s)
    engine = QuantXEngine(settings=s, risk_manager=risk, market_data=data)
    hunter = OpportunityHunter(engine=engine, market_data=data, db=db)
    out = hunter.hunt_and_process(
        seed_symbols=["RELIANCE"],
        top_n=3,
        enable_fno=False,
        trade_types=["SWING", "INTRADAY"],
        style_ids=["all"],
        strategies_per_product=2,
    )
    assert sum(len(v) for v in out["strategies_used"].values()) > 0
    assert out["count"] >= 0  # recommendations list present


def test_execute_with_levels_and_fno_place(tmp_path, monkeypatch):
    # Isolate DB for API app
    db_path = tmp_path / "api.db"
    monkeypatch.setenv("QUANTX_DB_PATH", str(db_path))

    from quantx.api import main as api_main
    from quantx.core.models import Side, TradeType
    from quantx.portfolio.db import Database

    # Point module DB/portfolio to temp
    api_main.db = Database(db_path)
    api_main.portfolio.db = api_main.db
    api_main.broker.db = api_main.db
    api_main.broker.portfolio.db = api_main.db
    api_main.paper_agent.portfolio.db = api_main.db

    client = TestClient(api_main.app)

    # Execute from explicit levels (preserves SL/target)
    res = client.post(
        "/api/execute",
        json={
            "symbol": "RELIANCE",
            "trade_type": "SWING",
            "side": "BUY",
            "entry": 1000,
            "stop_loss": 980,
            "target_1": 1040,
            "target_2": 1060,
            "quantity": 1,
            "force": True,
            "reason": "test levels",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "status" in body
    assert body["recommendation"]["stop_loss"] == 980
    assert body["recommendation"]["target_1"] == 1040

    # F&O futures place with auto levels
    fo = client.post(
        "/api/fno/place",
        json={
            "symbol": "NIFTY",
            "product": "FUTURES",
            "side": "BUY",
            "quantity": 1,
            "auto_levels": True,
        },
    )
    assert fo.status_code == 200, fo.text
    fob = fo.json()
    assert fob["product"] == "FUTURES"
    assert fob["levels"]["stop_loss"] > 0
    assert fob["levels"]["target_1"] > 0

    opt = client.post(
        "/api/fno/place",
        json={
            "symbol": "NIFTY",
            "product": "OPTIONS",
            "side": "BUY",
            "quantity": 1,
            "option_type": "CE",
            "strike": 22000,
            "auto_levels": True,
        },
    )
    assert opt.status_code == 200, opt.text
    assert opt.json()["product"] == "OPTIONS"
    assert opt.json()["levels"]["stop_loss"] < opt.json()["levels"]["entry"]

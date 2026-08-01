# QuantX

Institutional-grade AI trading agent for **NSE / BSE**.

**Primary mission:** maximize long-term risk-adjusted returns while protecting trading capital.

> You are NOT a gambling bot. Capital preservation is always first.

## Principles

- Never risk more than **1%** of capital per trade
- Maximum daily loss **2%** · weekly loss **5%**
- Minimum risk-reward **1:2**
- Stop after **3 consecutive losses**
- Never average losing trades
- Never remove stop losses
- Never revenge trade / overtrade

## Stack

| Layer | Tech |
|-------|------|
| Agent engine | Python 3.12 |
| API | FastAPI |
| Data | yfinance (NSE) + synthetic fallback |
| Persistence | SQLite |
| Dashboard | React + Vite + TypeScript |

## Quick start

```bash
# Backend
cd backend
pip install -r requirements.txt
cd ..
PYTHONPATH=backend python -m uvicorn quantx.api.main:app --host 0.0.0.0 --port 8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

Open http://localhost:5173 — API docs at http://localhost:8000/docs

## Paper trading (default)

QuantX is a **paper trading agent** by default (`config/settings.yaml` → `mode: paper`).

### What paper mode does
- Scans NSE watchlist on a timer (or one-shot cycle)
- Only enters when trend + momentum + volume + RR + risk gates align
- Fills with **adverse slippage** (5 bps default) and **NSE-style fees**
- Marks positions to market, trails stops, exits on SL / T1 / T2
- Never averages into an existing symbol
- Kill switch / panic exit / account reset

### Dashboard
```bash
make api          # http://localhost:8000
make frontend     # http://localhost:5173
```
Use **Start Auto** for continuous paper sessions, or **Run Cycle** for a single scan/execute/MTM pass.

### CLI
```bash
PYTHONPATH=backend python3 -m quantx.cli paper cycle
PYTHONPATH=backend python3 -m quantx.cli paper start --interval 60
PYTHONPATH=backend python3 -m quantx.cli analyze RELIANCE
PYTHONPATH=backend python3 -m quantx.cli portfolio
PYTHONPATH=backend python3 -m quantx.cli performance
PYTHONPATH=backend python3 -m quantx.cli paper reset --confirm
```

### API
| Endpoint | Purpose |
|----------|---------|
| `POST /api/paper/start` | Start auto paper session |
| `POST /api/paper/stop` | Stop session |
| `POST /api/paper/cycle` | One scan/execute/MTM cycle |
| `GET  /api/paper/status` | Session + portfolio + risk |
| `POST /api/paper/reset` | Reset capital (confirm=true) |
| `GET  /api/performance` | Win rate, expectancy, fees |

Live broker credentials must be supplied via environment variables only — never commit secrets.

```
QUANTX_BROKER_API_KEY=
QUANTX_BROKER_API_SECRET=
QUANTX_BROKER_ACCESS_TOKEN=
```

## Core modules

```
backend/quantx/
  core/          risk, sizing, engine, market hours, emergency
  analysis/      technical, fundamental, options, futures, macro
  data/          market data provider
  execution/     paper broker (duplicate-safe)
  portfolio/     positions, journal, SQLite
  reports/       morning / open / intraday / closing / weekly / risk
  api/           FastAPI routes
```

## Recommendation format

Every recommendation includes:

Market Direction · Trade Type · Entry · Stop Loss · Target 1/2 · Risk Reward · Probability · Confidence · Reason · Supporting Indicators · Fundamental Summary · Options Summary · Risk Notes · Alternative Scenario

Plus AI scores: Confidence, Risk, Volatility, Probability of Success, Expected Return, Expected Drawdown.

## Risk gates

Trades are rejected unless:

1. Trend confirmed  
2. Momentum confirmed  
3. Volume confirmed  
4. Risk-reward ≥ 1:2  
5. Position size ≤ 1% capital at risk  
6. Fundamental score acceptable  
7. Macro / VIX regime not blocking  
8. Portfolio risk limits clear  

## Emergency controls

- **Kill switch** — block all new trades  
- **Panic exit** — flatten all positions + lock trading  
- **Max drawdown lock** — auto-halt at configured drawdown  
- **Manual override** — explicit operator bypass (logged)  

## Tests

```bash
PYTHONPATH=backend python -m pytest tests/ -v
```

## Disclaimer

QuantX is a research / paper-trading system. Past signals do not guarantee future results. Live trading involves substantial risk of loss. Use at your own risk and comply with all applicable securities regulations.

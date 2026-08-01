import { useCallback, useEffect, useState } from 'react'
import {
  api,
  inr,
  inrDec,
  pct,
  type EmergencyState,
  type JournalEntry,
  type MarketSession,
  type PortfolioSnapshot,
  type Position,
  type Quote,
  type RiskStatus,
  type TradeRecommendation,
} from './api'
import './index.css'

const DEFAULT_SYMBOLS = 'RELIANCE,TCS,INFY,HDFCBANK,ICICIBANK,SBIN,ITC,LT'

function ScoreBar({
  label,
  value,
  risk,
}: {
  label: string
  value: number
  risk?: boolean
}) {
  return (
    <div className="score-bar">
      <label>
        <span>{label}</span>
        <span>{value.toFixed(0)}</span>
      </label>
      <div className="track">
        <div className={`fill${risk ? ' risk' : ''}`} style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
      </div>
    </div>
  )
}

export default function App() {
  const [portfolio, setPortfolio] = useState<PortfolioSnapshot | null>(null)
  const [risk, setRisk] = useState<RiskStatus | null>(null)
  const [session, setSession] = useState<MarketSession | null>(null)
  const [positions, setPositions] = useState<Position[]>([])
  const [journal, setJournal] = useState<JournalEntry[]>([])
  const [watchlist, setWatchlist] = useState<Quote[]>([])
  const [emergency, setEmergency] = useState<EmergencyState | null>(null)
  const [symbols, setSymbols] = useState(DEFAULT_SYMBOLS)
  const [recs, setRecs] = useState<TradeRecommendation[]>([])
  const [selected, setSelected] = useState<TradeRecommendation | null>(null)
  const [reportText, setReportText] = useState('')
  const [status, setStatus] = useState('Ready — paper trading mode')
  const [busy, setBusy] = useState(false)
  const [paperRunning, setPaperRunning] = useState(false)
  const [paperMsg, setPaperMsg] = useState('Session idle')
  const [paperStats, setPaperStats] = useState({ cycles: 0, executed: 0, rejected: 0, valid: 0 })
  const [perf, setPerf] = useState<{
    trades: number
    win_rate: number
    total_realized_pnl: number
    expectancy: number
    total_fees: number
  } | null>(null)
  const [strategy, setStrategy] = useState('swing_trend')
  const [btSymbol, setBtSymbol] = useState('RELIANCE')
  const [btResult, setBtResult] = useState<string>('')
  const [brokerInfo, setBrokerInfo] = useState('')

  const refresh = useCallback(async () => {
    try {
      const [p, r, s, pos, j, w, e, paper, performance] = await Promise.all([
        api.portfolio(),
        api.risk(),
        api.session(),
        api.positions(),
        api.journal(),
        api.watchlist(),
        api.emergency(),
        api.paperStatus(),
        api.performance(),
      ])
      setPortfolio(p)
      setRisk(r)
      setSession(s)
      setPositions(pos)
      setJournal(j)
      setWatchlist(w)
      setEmergency(e)
      setPaperRunning(paper.session.running)
      setPaperMsg(paper.session.last_message)
      setPaperStats({
        cycles: paper.session.cycles,
        executed: paper.session.executed,
        rejected: paper.session.rejected,
        valid: paper.session.valid_signals,
      })
      setPerf(performance)
    } catch (err) {
      setStatus(`Backend offline — start API on :8000 (${(err as Error).message})`)
    }
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, paperRunning ? 8000 : 20000)
    return () => clearInterval(id)
  }, [refresh, paperRunning])

  const startPaper = async () => {
    setBusy(true)
    try {
      const list = symbols.split(',').map((s) => s.trim()).filter(Boolean)
      await api.paperStart(list, 60, true)
      setStatus('Paper session STARTED — auto scan/execute under risk gates')
      await refresh()
    } catch (err) {
      setStatus(`Start failed: ${(err as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  const stopPaper = async () => {
    setBusy(true)
    try {
      await api.paperStop()
      setStatus('Paper session stopped')
      await refresh()
    } catch (err) {
      setStatus(`Stop failed: ${(err as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  const runPaperCycle = async () => {
    setBusy(true)
    setStatus('Running one paper cycle…')
    try {
      const res = await api.paperCycle()
      const signals = (res.signals as TradeRecommendation[] | undefined) || []
      if (signals.length) {
        setRecs(signals)
        setSelected(signals.find((x) => x.valid) || signals[0])
      }
      setStatus(String(res.message || 'Cycle complete'))
      await refresh()
    } catch (err) {
      setStatus(`Cycle failed: ${(err as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  const resetPaper = async () => {
    if (!confirm('Reset paper account to ₹10,00,000 and clear open positions?')) return
    setBusy(true)
    try {
      await api.paperReset(true)
      setStatus('Paper account reset')
      setRecs([])
      setSelected(null)
      await refresh()
    } catch (err) {
      setStatus(`Reset failed: ${(err as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  const runBacktest = async () => {
    setBusy(true)
    setStatus(`Backtesting ${btSymbol} / ${strategy}…`)
    try {
      const r = await api.backtest(btSymbol.trim().toUpperCase(), strategy, '1y')
      setBtResult(
        `${r.symbol} · ${r.strategy}\nReturn ${r.total_return_pct.toFixed(2)}% · PnL ${inr(r.total_pnl)}\n` +
          `Trades ${r.trades} · Win ${r.win_rate.toFixed(1)}% · MaxDD ${r.max_drawdown_pct.toFixed(2)}%\n` +
          `Expectancy ${inr(r.expectancy)} · Fees ${inr(r.total_fees)}\n` +
          (r.notes || []).join(' · '),
      )
      setStatus(`Backtest done — ${r.symbol} ${r.total_return_pct.toFixed(2)}%`)
    } catch (err) {
      setStatus(`Backtest failed: ${(err as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  const loadBroker = async () => {
    try {
      const b = await api.brokerStatus()
      setBrokerInfo(`${b.name} · ${b.mode} · ${b.message} · live_ready=${b.live_ready}`)
    } catch (err) {
      setBrokerInfo((err as Error).message)
    }
  }

  const runScan = async () => {
    setBusy(true)
    setStatus('Scanning watchlist with risk gates…')
    try {
      const list = symbols.split(',').map((s) => s.trim()).filter(Boolean)
      const res = await api.scan(list)
      setRecs(res.recommendations)
      const firstValid = res.recommendations.find((x) => x.valid) || res.recommendations[0] || null
      setSelected(firstValid)
      setStatus(`Scan complete — ${res.valid_count}/${res.count} actionable setups`)
      await refresh()
    } catch (err) {
      setStatus(`Scan failed: ${(err as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  const analyzeOne = async (symbol: string) => {
    setBusy(true)
    setStatus(`Analyzing ${symbol}…`)
    try {
      const rec = await api.analyze(symbol)
      setSelected(rec)
      setRecs((prev) => {
        const rest = prev.filter((r) => r.symbol !== rec.symbol)
        return [rec, ...rest]
      })
      setStatus(rec.valid ? `${symbol} — VALID setup` : `${symbol} — NO TRADE (${rec.rejection_reason})`)
    } catch (err) {
      setStatus(`Analyze failed: ${(err as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  const executeSelected = async () => {
    if (!selected) return
    setBusy(true)
    setStatus(`Executing ${selected.symbol} (paper)…`)
    try {
      const res = await api.execute(selected.symbol)
      setStatus(`${res.status}: ${res.message}`)
      setSelected(res.recommendation)
      await refresh()
    } catch (err) {
      setStatus(`Execute failed: ${(err as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  const mark = async () => {
    setBusy(true)
    try {
      await api.mark()
      setStatus('Marked positions to market / checked exits')
      await refresh()
    } catch (err) {
      setStatus(`Mark failed: ${(err as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  const closePos = async (id: number) => {
    setBusy(true)
    try {
      await api.close(id)
      setStatus(`Closed position #${id}`)
      await refresh()
    } catch (err) {
      setStatus(`Close failed: ${(err as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  const toggleKill = async () => {
    const active = !emergency?.kill_switch
    const e = await api.killSwitch(active)
    setEmergency(e)
    setStatus(active ? 'KILL SWITCH ON' : 'Kill switch cleared')
    await refresh()
  }

  const panic = async () => {
    if (!confirm('Panic exit ALL positions and engage kill switch?')) return
    setBusy(true)
    try {
      const res = await api.panicExit()
      setEmergency(res.emergency)
      setStatus(`Panic exit — closed ${res.closed.length} positions`)
      await refresh()
    } finally {
      setBusy(false)
    }
  }

  const loadReport = async (type: string) => {
    setBusy(true)
    try {
      const r = await api.report(type)
      setReportText(`${r.title}\n\n${r.summary}\n\n${JSON.stringify(r.sections, null, 2)}`)
      setStatus(`Loaded ${type} report`)
    } catch (err) {
      setStatus(`Report failed: ${(err as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  const halted = portfolio?.trading_halted || emergency?.kill_switch

  return (
    <div className="app">
      <header className="masthead">
        <div className="brand-block">
          <h1 className="brand">QuantX</h1>
          <p className="tagline">
            Paper trading agent for NSE/BSE — capital preservation first. Auto-scan, sized entries,
            slippage + fees, trailing stops. Never revenge trade.
          </p>
        </div>
        <div className="session-chip">
          <span className={`badge${paperRunning ? ' open' : ''}${halted ? ' halted' : ''}`}>
            <span className="dot" />
            {halted ? 'HALTED' : paperRunning ? 'PAPER LIVE' : session?.phase?.replace('_', ' ') || '…'} · paper
          </span>
          <span>{session?.server_time_ist || '—'}</span>
        </div>
      </header>

      {(halted || (emergency?.messages?.length ?? 0) > 0) && (
        <div className="emergency-banner">
          {portfolio?.halt_reason || emergency?.messages?.join(' · ') || 'Trading halted'}
        </div>
      )}

      <section className="metrics">
        <div className="metric">
          <label>Capital</label>
          <strong>{portfolio ? inr(portfolio.capital) : '—'}</strong>
        </div>
        <div className="metric">
          <label>Total PnL</label>
          <strong className={(portfolio?.total_pnl || 0) >= 0 ? 'pos' : 'neg'}>
            {portfolio ? inr(portfolio.total_pnl) : '—'}
          </strong>
        </div>
        <div className="metric">
          <label>Unrealized</label>
          <strong className={(portfolio?.unrealized_pnl || 0) >= 0 ? 'pos' : 'neg'}>
            {portfolio ? inr(portfolio.unrealized_pnl) : '—'}
          </strong>
        </div>
        <div className="metric">
          <label>Drawdown</label>
          <strong className={(portfolio?.drawdown_pct || 0) > 2 ? 'warn' : ''}>
            {portfolio ? `${portfolio.drawdown_pct.toFixed(2)}%` : '—'}
          </strong>
        </div>
        <div className="metric">
          <label>Open / Max</label>
          <strong>
            {portfolio?.open_positions ?? '—'} / {risk ? risk.open_positions >= 0 ? 5 : 5 : 5}
          </strong>
        </div>
        <div className="metric">
          <label>Can Trade</label>
          <strong className={risk?.can_trade ? 'pos' : 'neg'}>
            {risk ? (risk.can_trade ? 'YES' : 'NO') : '—'}
          </strong>
        </div>
      </section>

      <div className="grid">
        <div className="stack">
          <section className="panel">
            <div className="panel-head">
              <h2>Signal Scanner</h2>
              <div className="actions">
                <button className="btn primary" disabled={busy} onClick={runScan}>
                  Scan
                </button>
                <button className="btn" disabled={busy || !selected} onClick={executeSelected}>
                  Execute Paper
                </button>
              </div>
            </div>
            <div className="scan-row">
              <input
                value={symbols}
                onChange={(e) => setSymbols(e.target.value)}
                placeholder="NSE symbols, comma-separated"
              />
            </div>
            {recs.length === 0 ? (
              <p className="empty">Run a scan to generate recommendations with confidence & risk scores.</p>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Side</th>
                    <th>Conf</th>
                    <th>RR</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {recs.map((r) => (
                    <tr
                      key={r.symbol + r.generated_at}
                      onClick={() => setSelected(r)}
                      style={{ cursor: 'pointer', outline: selected?.symbol === r.symbol ? '1px solid rgba(62,207,172,0.35)' : undefined }}
                    >
                      <td>{r.symbol}</td>
                      <td>
                        <span className={`pill ${r.side === 'BUY' ? 'buy' : 'sell'}`}>{r.side}</span>
                      </td>
                      <td>{r.scores.confidence.toFixed(0)}</td>
                      <td>{r.risk_reward.toFixed(2)}</td>
                      <td>
                        <span className={`pill ${r.valid ? 'valid' : 'invalid'}`}>
                          {r.valid ? 'VALID' : 'REJECT'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>Recommendation Detail</h2>
            </div>
            {!selected ? (
              <p className="empty">Select a recommendation to inspect entry, stops, targets, and rationale.</p>
            ) : (
              <div className="rec-detail">
                <div className="rec-grid">
                  <div className="rec-cell">
                    <span>Direction</span>
                    <strong>{selected.market_direction}</strong>
                  </div>
                  <div className="rec-cell">
                    <span>Entry</span>
                    <strong>{inrDec(selected.entry)}</strong>
                  </div>
                  <div className="rec-cell">
                    <span>Stop</span>
                    <strong className="neg">{inrDec(selected.stop_loss)}</strong>
                  </div>
                  <div className="rec-cell">
                    <span>T1 / T2</span>
                    <strong className="pos">
                      {inrDec(selected.target_1)} / {inrDec(selected.target_2)}
                    </strong>
                  </div>
                  <div className="rec-cell">
                    <span>Qty</span>
                    <strong>{selected.quantity}</strong>
                  </div>
                  <div className="rec-cell">
                    <span>Risk ₹</span>
                    <strong>{inr(selected.capital_at_risk)}</strong>
                  </div>
                  <div className="rec-cell">
                    <span>RR</span>
                    <strong>1:{selected.risk_reward.toFixed(2)}</strong>
                  </div>
                  <div className="rec-cell">
                    <span>P(success)</span>
                    <strong>{selected.scores.probability_of_success.toFixed(0)}%</strong>
                  </div>
                </div>

                <div className="scores">
                  <ScoreBar label="Confidence" value={selected.scores.confidence} />
                  <ScoreBar label="Risk" value={selected.scores.risk_score} risk />
                  <ScoreBar label="Volatility" value={selected.scores.volatility_score} risk />
                </div>

                <p className="prose">
                  <strong>Reason.</strong> {selected.reason}
                </p>
                <p className="prose">
                  <strong>Fundamental.</strong> {selected.fundamental_summary}
                </p>
                <p className="prose">
                  <strong>Options.</strong> {selected.options_summary}
                </p>
                <p className="prose">
                  <strong>Risk notes.</strong> {selected.risk_notes}
                </p>
                <p className="prose">
                  <strong>Alternative.</strong> {selected.alternative_scenario}
                </p>
                <div className="chips">
                  {selected.supporting_indicators.map((i) => (
                    <span className="chip" key={i}>
                      {i}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </section>
        </div>

        <div className="stack">
          <section className="panel">
            <div className="panel-head">
              <h2>Paper Session</h2>
              <div className="actions">
                {!paperRunning ? (
                  <button className="btn primary" disabled={busy} onClick={startPaper}>
                    Start Auto
                  </button>
                ) : (
                  <button className="btn warn" disabled={busy} onClick={stopPaper}>
                    Stop
                  </button>
                )}
                <button className="btn" disabled={busy} onClick={runPaperCycle}>
                  Run Cycle
                </button>
                <button className="btn" disabled={busy || paperRunning} onClick={resetPaper}>
                  Reset
                </button>
              </div>
            </div>
            <div className="rec-grid">
              <div className="rec-cell">
                <span>Status</span>
                <strong className={paperRunning ? 'pos' : ''}>{paperRunning ? 'RUNNING' : 'IDLE'}</strong>
              </div>
              <div className="rec-cell">
                <span>Cycles</span>
                <strong>{paperStats.cycles}</strong>
              </div>
              <div className="rec-cell">
                <span>Executed</span>
                <strong className="pos">{paperStats.executed}</strong>
              </div>
              <div className="rec-cell">
                <span>Rejected</span>
                <strong className="warn">{paperStats.rejected}</strong>
              </div>
            </div>
            <p className="prose" style={{ marginTop: '0.75rem' }}>
              <strong>Last cycle.</strong> {paperMsg}
            </p>
            {perf && (
              <p className="prose">
                <strong>Performance.</strong> {perf.trades} trades · win {perf.win_rate.toFixed(1)}% · realized{' '}
                <span className={perf.total_realized_pnl >= 0 ? 'pos' : 'neg'}>{inr(perf.total_realized_pnl)}</span> ·
                expectancy {inr(perf.expectancy)} · fees {inr(perf.total_fees)}
              </p>
            )}
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>Positions</h2>
              <div className="actions">
                <button className="btn" disabled={busy} onClick={mark}>
                  Mark MTM
                </button>
              </div>
            </div>
            {positions.length === 0 ? (
              <p className="empty">No open positions.</p>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Sym</th>
                    <th>Side</th>
                    <th>PnL</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {positions.map((p) => (
                    <tr key={p.id}>
                      <td>
                        {p.symbol}
                        <div style={{ color: 'var(--muted)', fontSize: '0.7rem' }}>
                          {p.quantity} @ {p.entry_price.toFixed(1)}
                        </div>
                      </td>
                      <td>
                        <span className={`pill ${p.side === 'BUY' ? 'buy' : 'sell'}`}>{p.side}</span>
                      </td>
                      <td className={p.pnl >= 0 ? 'pos' : 'neg'}>
                        {inr(p.pnl)}
                        <div style={{ fontSize: '0.7rem' }}>{pct(p.pnl_pct)}</div>
                      </td>
                      <td>
                        <button className="btn" onClick={() => closePos(p.id)}>
                          Close
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>Emergency</h2>
              <div className="actions">
                <button className="btn warn" onClick={toggleKill}>
                  {emergency?.kill_switch ? 'Clear Kill' : 'Kill Switch'}
                </button>
                <button className="btn danger" disabled={busy} onClick={panic}>
                  Panic Exit
                </button>
              </div>
            </div>
            <p className="prose">
              Risk budget/trade {risk ? inr(risk.risk_budget_remaining) : '—'} · Daily loss used{' '}
              {risk ? `${risk.daily_loss_used_pct.toFixed(2)}%` : '—'} · Consecutive losses{' '}
              {risk?.consecutive_losses ?? '—'}
            </p>
            {risk?.reasons?.length ? (
              <p className="prose warn">{risk.reasons.join(' · ')}</p>
            ) : (
              <p className="prose">All risk gates green.</p>
            )}
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>Watchlist</h2>
            </div>
            <div className="watchlist">
              {watchlist
                .filter((q) => !q.symbol.startsWith('^') && q.symbol !== 'INR=X')
                .slice(0, 10)
                .map((q) => (
                  <button key={q.symbol} className="wl-item" onClick={() => analyzeOne(q.symbol)} type="button">
                    <div className="sym">{q.symbol}</div>
                    <div className="px">{q.price.toFixed(1)}</div>
                    <div className={q.change_pct >= 0 ? 'pos' : 'neg'} style={{ fontSize: '0.75rem' }}>
                      {pct(q.change_pct)}
                    </div>
                  </button>
                ))}
            </div>
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>Backtest</h2>
              <div className="actions">
                <button className="btn primary" disabled={busy} onClick={runBacktest}>
                  Run 1Y
                </button>
                <button className="btn" onClick={loadBroker}>
                  Broker
                </button>
              </div>
            </div>
            <div className="scan-row">
              <input value={btSymbol} onChange={(e) => setBtSymbol(e.target.value)} placeholder="Symbol" />
              <select
                value={strategy}
                onChange={(e) => setStrategy(e.target.value)}
                style={{
                  background: 'var(--bg-0)',
                  color: 'var(--text)',
                  border: '1px solid var(--line)',
                  borderRadius: 8,
                  padding: '0.55rem 0.75rem',
                }}
              >
                <option value="swing_trend">swing_trend</option>
                <option value="breakout">breakout</option>
                <option value="intraday_mean_reversion">intraday_mean_reversion</option>
              </select>
            </div>
            {btResult ? <pre className="report-box">{btResult}</pre> : <p className="empty">Run a 1Y backtest under the same 1% risk rules.</p>}
            {brokerInfo && <p className="prose">{brokerInfo}</p>}
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>Reports</h2>
              <div className="actions">
                {['morning', 'intraday', 'closing', 'weekly', 'risk'].map((t) => (
                  <button key={t} className="btn" disabled={busy} onClick={() => loadReport(t)}>
                    {t}
                  </button>
                ))}
              </div>
            </div>
            {reportText ? <pre className="report-box">{reportText}</pre> : <p className="empty">Generate a report.</p>}
          </section>

          <section className="panel">
            <h2>Trade Journal</h2>
            {journal.length === 0 ? (
              <p className="empty">No closed trades yet.</p>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Sym</th>
                    <th>PnL</th>
                    <th>Lesson</th>
                  </tr>
                </thead>
                <tbody>
                  {journal.slice(0, 8).map((j) => (
                    <tr key={j.id}>
                      <td>{j.symbol}</td>
                      <td className={j.pnl >= 0 ? 'pos' : 'neg'}>{inr(j.pnl)}</td>
                      <td style={{ fontFamily: 'var(--font)', color: 'var(--muted)' }}>{j.lessons || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </div>
      </div>

      <p className="status-line">{status}</p>
    </div>
  )
}

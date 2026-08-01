import { useCallback, useEffect, useState } from 'react'
import {
  api,
  inr,
  inrDec,
  pct,
  type EmergencyState,
  type JournalEntry,
  type MarginBook,
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
  const [cycles, setCycles] = useState<
    Array<{
      id: number
      cycle_no: number
      message: string
      mtm_closed: number
      scanned: number
      valid_count: number
      executed_count: number
      rejected_count: number
      skipped_count?: number
      executed: Array<{ symbol: string; side?: string; quantity?: number; fill_price?: number; reason?: string; status?: string; message?: string }>
      rejected: Array<{ symbol: string; reason: string; trade_type?: string; status?: string }>
      skipped?: Array<{ symbol: string; reason: string; trade_type?: string; status?: string }>
      created_at: string
    }>
  >([])
  const [paperStats, setPaperStats] = useState({ cycles: 0, executed: 0, rejected: 0, skipped: 0, valid: 0 })
  const [perf, setPerf] = useState<{
    trades: number
    win_rate: number
    total_realized_pnl: number
    expectancy: number
    total_fees: number
  } | null>(null)
  const [strategy, setStrategy] = useState('swing_trend')
  const [enableFno, setEnableFno] = useState(true)
  const [scanProduct, setScanProduct] = useState<'ALL' | 'SWING' | 'FUTURES' | 'OPTIONS'>('ALL')
  const [scanStyle, setScanStyle] = useState<string>('all')
  const [huntMarket, setHuntMarket] = useState(true)
  const [hunted, setHunted] = useState<
    Array<{
      symbol: string
      score: number
      change_pct: number
      volume_ratio: number
      reasons: string[]
      is_index?: boolean
    }>
  >([])
  const [huntMeta, setHuntMeta] = useState('')
  const [capitalDelta, setCapitalDelta] = useState('100000')
  const [regimeInfo, setRegimeInfo] = useState<{
    regime: string
    summary: string
    confidence: number
  } | null>(null)
  const [strategyCount, setStrategyCount] = useState(0)
  const [learningTop, setLearningTop] = useState<Array<{ strategy: string; weight: number; win_rate: number; pnl: number }>>([])
  const [strategyCatalog, setStrategyCatalog] = useState<
    Array<{ name: string; trade_type: string; family?: string; description?: string }>
  >([])
  const [styleGroups, setStyleGroups] = useState<Array<{ id: string; name: string; strategies: string[] }>>([])
  const [btSymbol, setBtSymbol] = useState('RELIANCE')
  const [btResult, setBtResult] = useState<string>('')
  const [brokerInfo, setBrokerInfo] = useState('')
  const [tab, setTab] = useState<'overview' | 'orders' | 'cycles' | 'pnl' | 'chat' | 'margin' | 'search'>('overview')
  const [marginBook, setMarginBook] = useState<MarginBook | null>(null)
  const [searchQuery, setSearchQuery] = useState('NIFTY')
  const [searchResults, setSearchResults] = useState<
    Array<{
      symbol: string
      exchange: string
      price: number | null
      change_pct: number | null
      is_index: boolean
      fno: boolean
      segment: string
    }>
  >([])
  const [searchSymbol, setSearchSymbol] = useState('NIFTY')
  const [fnoOverview, setFnoOverview] = useState<Awaited<ReturnType<typeof api.fnoOverview>> | null>(null)
  const [selectedExpiry, setSelectedExpiry] = useState('')
  const [optionChain, setOptionChain] = useState<Awaited<ReturnType<typeof api.fnoChain>> | null>(null)
  const [searchBusy, setSearchBusy] = useState(false)
  const [searchError, setSearchError] = useState('')
  const [orders, setOrders] = useState<
    Array<{
      id: number
      client_order_id: string
      symbol: string
      side: string
      quantity: number
      price: number
      status: string
      message: string
      created_at: string
    }>
  >([])
  const [pnlData, setPnlData] = useState<Awaited<ReturnType<typeof api.pnl>> | null>(null)
  const [chatInput, setChatInput] = useState('')
  const [chatBusy, setChatBusy] = useState(false)
  const [chatMessages, setChatMessages] = useState<Array<{ role: 'user' | 'assistant'; content: string; timestamp?: string; mode?: string }>>([
    {
      role: 'assistant',
      content:
        'QuantX LLM coach ready. Connect an API key (Groq / OpenAI / OpenRouter) in LLM settings, then ask why trades lost money or click Analyze losses for a full post-mortem.',
      timestamp: new Date().toISOString(),
      mode: 'system',
    },
  ])
  const [llmCfg, setLlmCfg] = useState<{
    enabled: boolean
    provider: string
    model: string
    has_api_key: boolean
    mode: string
    hint?: string
    key_source?: string
  } | null>(null)
  const [llmKey, setLlmKey] = useState('')
  const [llmProvider, setLlmProvider] = useState('groq')
  const [llmModel, setLlmModel] = useState('')
  const [showLlmSettings, setShowLlmSettings] = useState(false)

  const refresh = useCallback(async () => {
    const settled = await Promise.allSettled([
      api.portfolio(),
      api.risk(),
      api.session(),
      api.positions(),
      api.journal(),
      api.watchlist(),
      api.emergency(),
      api.paperStatus(),
      api.performance(),
      api.orders(),
      api.cycles(),
      api.pnl(),
      api.margin(),
    ])
    const val = <T,>(i: number): T | null =>
      settled[i].status === 'fulfilled' ? (settled[i] as PromiseFulfilledResult<T>).value : null

    const p = val<Awaited<ReturnType<typeof api.portfolio>>>(0)
    const r = val<Awaited<ReturnType<typeof api.risk>>>(1)
    const s = val<Awaited<ReturnType<typeof api.session>>>(2)
    const pos = val<Awaited<ReturnType<typeof api.positions>>>(3)
    const j = val<Awaited<ReturnType<typeof api.journal>>>(4)
    const w = val<Awaited<ReturnType<typeof api.watchlist>>>(5)
    const e = val<Awaited<ReturnType<typeof api.emergency>>>(6)
    const paper = val<Awaited<ReturnType<typeof api.paperStatus>>>(7)
    const performance = val<Awaited<ReturnType<typeof api.performance>>>(8)
    const ords = val<Awaited<ReturnType<typeof api.orders>>>(9)
    const cyc = val<Awaited<ReturnType<typeof api.cycles>>>(10)
    const pnl = val<Awaited<ReturnType<typeof api.pnl>>>(11)
    const margin = val<Awaited<ReturnType<typeof api.margin>>>(12)

    const failed = settled
      .map((x, i) => (x.status === 'rejected' ? i : -1))
      .filter((i) => i >= 0)
    if (!p && !r) {
      const firstErr = settled.find((x) => x.status === 'rejected') as PromiseRejectedResult | undefined
      setStatus(`Backend offline — start API on :8000 (${firstErr?.reason?.message || 'unreachable'})`)
      return
    }
    if (p) setPortfolio(p)
    if (r) setRisk(r)
    if (s) setSession(s)
    if (pos) setPositions(pos)
    if (j) setJournal(j)
    if (w) setWatchlist(w)
    if (e) setEmergency(e)
    if (paper) {
      setPaperRunning(paper.session.running)
      setPaperMsg(paper.session.last_message)
      setPaperStats({
        cycles: paper.session.cycles,
        executed: paper.session.executed,
        rejected: paper.session.rejected,
        skipped: paper.session.skipped || 0,
        valid: paper.session.valid_signals,
      })
    }
    if (performance) setPerf(performance)
    if (ords) setOrders(ords)
    if (cyc) setCycles(cyc)
    if (pnl) setPnlData(pnl)
    if (margin) setMarginBook(margin)

    try {
      const [reg, stratPack, learn] = await Promise.all([api.regime(), api.strategies(), api.learning()])
      setRegimeInfo({
        regime: reg.regime.regime,
        summary: reg.regime.summary,
        confidence: reg.regime.confidence,
      })
      setStrategyCount(stratPack.count)
      setStrategyCatalog(stratPack.strategies || [])
      setStyleGroups(
        (stratPack.styles || []).map((s) => ({
          id: s.id,
          name: s.name,
          strategies: s.strategies_available?.length ? s.strategies_available : s.strategies || [],
        })),
      )
      setLearningTop((learn.leaderboard || []).slice(0, 5))
      if (stratPack.strategies?.length && !stratPack.strategies.some((s) => s.name === strategy)) {
        setStrategy(stratPack.strategies[0].name)
      }
    } catch {
      /* optional endpoints */
    }
    try {
      const cfg = await api.llmConfig()
      setLlmCfg(cfg)
      setLlmProvider(cfg.provider || 'groq')
      setLlmModel(cfg.model || '')
    } catch {
      /* ignore */
    }
    if (failed.length && p) {
      setStatus('QuantX online — some optional endpoints unavailable (restart API if metrics look stale)')
    } else if (p) {
      setStatus((prev) => (prev.startsWith('Backend offline') ? 'Ready — paper trading mode' : prev))
    }
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, paperRunning ? 8000 : 20000)
    return () => clearInterval(id)
  }, [refresh, paperRunning])

  const styleIdsForRequest = (): string[] | undefined => {
    if (!scanStyle || scanStyle === 'auto') return undefined
    if (scanStyle === 'all') return ['all']
    return [scanStyle]
  }

  const startPaper = async () => {
    setBusy(true)
    try {
      const list = symbols.split(',').map((s) => s.trim()).filter(Boolean)
      const styleIds = styleIdsForRequest()
      const tradeTypes = enableFno
        ? ['SWING', 'INTRADAY', 'FUTURES', 'OPTIONS']
        : scanStyle !== 'auto'
          ? ['SWING', 'INTRADAY']
          : ['SWING']
      await api.paperStart(list, 60, true, {
        enable_fno: enableFno,
        trade_type: 'SWING',
        trade_types: tradeTypes,
        style_ids: styleIds,
      })
      const styleLabel =
        scanStyle === 'auto'
          ? 'regime selector'
          : scanStyle === 'all'
            ? 'all trading styles'
            : `style: ${styleGroups.find((g) => g.id === scanStyle)?.name || scanStyle}`
      setStatus(
        enableFno
          ? `Paper session STARTED — Stocks + F&O · ${styleLabel}`
          : `Paper session STARTED — equity · ${styleLabel}`,
      )
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

  const sendChat = async (preset?: string, intent = 'chat') => {
    const text = (preset ?? chatInput).trim()
    if (!text || chatBusy) return
    setChatBusy(true)
    setChatInput('')
    const userMsg = { role: 'user' as const, content: text, timestamp: new Date().toISOString() }
    setChatMessages((prev) => [...prev, userMsg])
    try {
      const history = [...chatMessages, userMsg].map((m) => ({ role: m.role, content: m.content }))
      const res = await api.chat(text, history, intent)
      setChatMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: res.content,
          timestamp: res.timestamp,
          mode: res.mode,
        },
      ])
      if (res.mode === 'fallback_rules') {
        setShowLlmSettings(true)
      }
    } catch (err) {
      setChatMessages((prev) => [
        ...prev,
        { role: 'assistant', content: `Chat error: ${(err as Error).message}`, timestamp: new Date().toISOString() },
      ])
    } finally {
      setChatBusy(false)
    }
  }

  const analyzeLosses = async () => {
    setChatBusy(true)
    const userMsg = {
      role: 'user' as const,
      content: 'Analyze my losses — what did strategy/logic miss?',
      timestamp: new Date().toISOString(),
    }
    setChatMessages((prev) => [...prev, userMsg])
    try {
      const history = [...chatMessages, userMsg].map((m) => ({ role: m.role, content: m.content }))
      const res = await api.reviewLosses(history)
      setChatMessages((prev) => [
        ...prev,
        { role: 'assistant', content: res.content, timestamp: res.timestamp, mode: res.mode },
      ])
      if (res.mode === 'fallback_rules') setShowLlmSettings(true)
    } catch (err) {
      setChatMessages((prev) => [
        ...prev,
        { role: 'assistant', content: `Loss review error: ${(err as Error).message}`, timestamp: new Date().toISOString() },
      ])
    } finally {
      setChatBusy(false)
    }
  }

  const saveLlm = async () => {
    setChatBusy(true)
    try {
      const cfg = await api.setLlmConfig(llmKey, llmProvider, llmModel)
      setLlmCfg(cfg)
      setLlmKey('')
      setChatMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: cfg.has_api_key
            ? `LLM enabled via ${cfg.provider} (${cfg.model}). Ask about losses or click Analyze losses.`
            : 'API key cleared. Chat will use fallback until a key is set.',
          timestamp: new Date().toISOString(),
          mode: cfg.mode,
        },
      ])
    } catch (err) {
      setStatus(`LLM config failed: ${(err as Error).message}`)
    } finally {
      setChatBusy(false)
    }
  }

  const adjustCapital = async (delta: number) => {
    setBusy(true)
    try {
      const res = await api.paperAdjustCapital({ delta, reason: 'Dashboard capital adjust' })
      setStatus(`Paper capital ${delta >= 0 ? 'increased' : 'decreased'}: ${inr(res.before)} → ${inr(res.after)}`)
      await refresh()
    } catch (err) {
      setStatus(`Capital adjust failed: ${(err as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  const runSymbolSearch = useCallback(async (q?: string) => {
    const query = (q ?? searchQuery).trim()
    setSearchBusy(true)
    setSearchError('')
    try {
      const res = await api.search(query || 'NIFTY')
      setSearchResults(res.results || [])
      setStatus(`Search: ${(res.results || []).length} symbols`)
    } catch (err) {
      const msg = (err as Error).message
      setSearchError(`Search failed: ${msg}`)
      setStatus(`Search failed: ${msg}`)
    } finally {
      setSearchBusy(false)
    }
  }, [searchQuery])

  const loadFnoSymbol = useCallback(async (symbol: string, expiry?: string) => {
    const sym = (symbol || 'NIFTY').toUpperCase()
    setSearchBusy(true)
    setSearchError('')
    setSearchSymbol(sym)
    setSearchQuery(sym)
    try {
      const ov = await api.fnoOverview(sym)
      setFnoOverview(ov)
      const exp = expiry || ov.default_expiry || ov.expiries?.[0]?.expiry || ''
      setSelectedExpiry(exp)
      if (exp) {
        const chain = await api.fnoChain(sym, exp)
        setOptionChain(chain)
      } else {
        setOptionChain(null)
      }
      setStatus(`Loaded F&O chain for ${sym} — expiries + CE/PE`)
    } catch (err) {
      setFnoOverview(null)
      setOptionChain(null)
      const msg = (err as Error).message
      setSearchError(`F&O load failed: ${msg}. Is the API running on :8000?`)
      setStatus(`F&O load failed: ${msg}`)
    } finally {
      setSearchBusy(false)
    }
  }, [])

  const changeExpiry = useCallback(async (expiry: string) => {
    setSelectedExpiry(expiry)
    if (!searchSymbol || !expiry) return
    setSearchBusy(true)
    setSearchError('')
    try {
      const chain = await api.fnoChain(searchSymbol, expiry)
      setOptionChain(chain)
      setStatus(`Option chain ${searchSymbol} · ${expiry}`)
    } catch (err) {
      const msg = (err as Error).message
      setSearchError(`Expiry chain failed: ${msg}`)
      setStatus(`Expiry chain failed: ${msg}`)
    } finally {
      setSearchBusy(false)
    }
  }, [searchSymbol])

  useEffect(() => {
    if (tab !== 'search' && tab !== 'margin') return
    void loadFnoSymbol(searchSymbol || 'NIFTY', selectedExpiry || undefined)
    if (tab === 'search') void runSymbolSearch(searchSymbol || 'NIFTY')
    // intentionally only when opening Search / F&O tabs
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab])

  const setCapitalAbsolute = async () => {
    const n = Number(capitalDelta)
    if (!Number.isFinite(n) || n < 10000) {
      setStatus('Enter a valid capital amount (≥ ₹10,000)')
      return
    }
    setBusy(true)
    try {
      const res = await api.paperAdjustCapital({ set_to: n, reason: 'Dashboard set capital' })
      setStatus(`Paper capital set to ${inr(res.after)}`)
      await refresh()
    } catch (err) {
      setStatus(`Set capital failed: ${(err as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  const runScan = async () => {
    setBusy(true)
    const list = symbols.split(',').map((s) => s.trim()).filter(Boolean)
    // Product dropdown is the source of truth for Scan (checkbox is kept in sync).
    const useFno = scanProduct === 'ALL'
    const product = scanProduct === 'ALL' ? 'SWING' : scanProduct
    const styleIds = styleIdsForRequest()
    const styleLabel =
      scanStyle === 'auto'
        ? ''
        : scanStyle === 'all'
          ? ' · all trading styles'
          : ` · ${styleGroups.find((g) => g.id === scanStyle)?.name || scanStyle}`
    setStatus(
      huntMarket
        ? `AI hunting market opportunities across universe, then processing via strategies${styleLabel}…`
        : useFno
          ? `Scanning Stocks + F&O${styleLabel}…`
          : scanProduct === 'SWING'
            ? `Scanning Stocks only${styleLabel}…`
            : `Scanning ${scanProduct} only${styleLabel}…`,
    )
    try {
      const res = await api.scan(list, {
        enable_fno: useFno,
        trade_type: product,
        hunt_market: huntMarket,
        top_n: 8,
        style_ids: styleIds,
      })
      const rows = res.recommendations || []
      setRecs(rows)
      const firstValid = rows.find((x) => x.valid) || rows[0] || null
      setSelected(firstValid)
      if (res.hunted && res.hunted.length) {
        setHunted(res.hunted)
        setSymbols((res.hunted_symbols || res.hunted.map((h) => h.symbol)).join(', '))
        const stratCount = Object.values(res.strategies_used || {}).reduce((n, arr) => n + (arr?.length || 0), 0)
        setHuntMeta(
          `${res.message || ''} · regime ${res.regime?.regime || '—'} · ${stratCount} strategies` +
            (res.styles_touched?.length
              ? ` · styles ${res.styles_touched
                  .slice(0, 6)
                  .map((s) => s.name)
                  .join(', ')}`
              : ''),
        )
      } else if (res.mode === 'trading_styles' && res.styles_touched?.length) {
        setHunted([])
        setHuntMeta(
          `${res.message || ''} · styles ${res.styles_touched
            .slice(0, 8)
            .map((s) => s.name)
            .join(', ')}`,
        )
      } else {
        setHunted([])
        setHuntMeta('')
      }
      setStatus(
        rows.length === 0
          ? 'Scan returned no recommendations'
          : `${res.mode === 'market_hunt' ? 'Hunt' : res.mode === 'trading_styles' ? 'Style scan' : 'Scan'} complete — ${res.valid_count}/${res.count} actionable · ${
              firstValid
                ? `selected ${firstValid.symbol} ${firstValid.trade_type}${firstValid.valid ? '' : ' (rejected setup)'}`
                : 'none selected'
            }`,
      )
      await refresh()
    } catch (err) {
      setRecs([])
      setSelected(null)
      setHunted([])
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
    setStatus(`Executing ${selected.symbol} ${selected.trade_type} (paper)…`)
    try {
      const res = await api.execute(selected.symbol, selected.trade_type)
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
          <label>Available Margin</label>
          <strong>{portfolio ? inr(portfolio.available_margin) : '—'}</strong>
        </div>
        <div className="metric">
          <label>Used Margin</label>
          <strong>{portfolio ? inr(portfolio.used_margin) : '—'}</strong>
        </div>
        <div className="metric">
          <label>Total PnL</label>
          <strong className={(portfolio?.total_pnl || 0) >= 0 ? 'pos' : 'neg'}>
            {portfolio ? inr(portfolio.total_pnl) : '—'}
          </strong>
        </div>
        <div className="metric">
          <label>Drawdown</label>
          <strong className={(portfolio?.drawdown_pct || 0) > 2 ? 'warn' : ''}>
            {portfolio ? `${portfolio.drawdown_pct.toFixed(2)}%` : '—'}
          </strong>
        </div>
        <div className="metric">
          <label>Can Trade</label>
          <strong className={risk?.can_trade ? 'pos' : 'neg'}>
            {risk ? (risk.can_trade ? 'YES' : 'NO') : '—'}
          </strong>
        </div>
      </section>

      <nav className="tabs">
        <button className={`tab${tab === 'overview' ? ' active' : ''}`} onClick={() => setTab('overview')} type="button">
          Overview
        </button>
        <button className={`tab${tab === 'search' ? ' active' : ''}`} onClick={() => setTab('search')} type="button">
          Search / F&amp;O
        </button>
        <button className={`tab${tab === 'margin' ? ' active' : ''}`} onClick={() => setTab('margin')} type="button">
          F&amp;O
          <span className="count">{marginBook?.fno_count || 0}</span>
        </button>
        <button className={`tab${tab === 'orders' ? ' active' : ''}`} onClick={() => setTab('orders')} type="button">
          Orders<span className="count">{orders.length}</span>
        </button>
        <button className={`tab${tab === 'pnl' ? ' active' : ''}`} onClick={() => setTab('pnl')} type="button">
          P&amp;L<span className="count">{pnlData ? (pnlData.summary.open_positions + pnlData.summary.closed_trades) : 0}</span>
        </button>
        <button className={`tab${tab === 'cycles' ? ' active' : ''}`} onClick={() => setTab('cycles')} type="button">
          Cycles<span className="count">{paperStats.cycles || cycles.length}</span>
        </button>
        <button className={`tab${tab === 'chat' ? ' active' : ''}`} onClick={() => setTab('chat')} type="button">
          Chat
        </button>
      </nav>

      {tab === 'search' && (
        <div className="margin-page search-fno-page">
          <section className="panel" style={{ marginBottom: '1rem' }}>
            <div className="panel-head">
              <h2>Search Stocks &amp; F&amp;O</h2>
              <div className="actions">
                <button
                  className="btn primary"
                  type="button"
                  disabled={searchBusy}
                  onClick={() => {
                    void runSymbolSearch()
                    void loadFnoSymbol(searchQuery || searchSymbol || 'NIFTY')
                  }}
                >
                  {searchBusy ? 'Loading…' : 'Search'}
                </button>
              </div>
            </div>
            <p className="empty" style={{ marginBottom: '0.75rem' }}>
              Type a symbol or tap a chip to see spot, futures, expiry dates, and Call (CE) / Put (PE) chain.
            </p>
            <div className="scan-row">
              <input
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value.toUpperCase())}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    void runSymbolSearch()
                    void loadFnoSymbol(searchQuery || 'NIFTY')
                  }
                }}
                placeholder="Search symbol e.g. NIFTY, BANKNIFTY, RELIANCE, TCS"
                aria-label="Search symbol"
              />
            </div>
            <div className="chips" style={{ marginBottom: '0.75rem' }}>
              {['NIFTY', 'BANKNIFTY', 'RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'SBIN'].map((s) => (
                <button
                  key={s}
                  type="button"
                  className="chip"
                  style={{
                    cursor: 'pointer',
                    border: 'none',
                    background: searchSymbol === s ? 'var(--teal-dim)' : undefined,
                    color: searchSymbol === s ? 'var(--teal)' : undefined,
                  }}
                  onClick={() => {
                    setSearchQuery(s)
                    void runSymbolSearch(s)
                    void loadFnoSymbol(s)
                  }}
                >
                  {s}
                </button>
              ))}
            </div>
            {searchError && (
              <div className="emergency-banner" style={{ marginBottom: '0.75rem' }}>
                {searchError}{' '}
                <button className="btn" type="button" onClick={() => void loadFnoSymbol(searchSymbol || 'NIFTY')}>
                  Retry
                </button>
              </div>
            )}
            {searchBusy && !fnoOverview && <p className="empty">Loading F&amp;O details for {searchSymbol || 'NIFTY'}…</p>}
            {searchResults.length > 0 && (
              <table className="table">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Segment</th>
                    <th>LTP</th>
                    <th>Chg%</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {searchResults.map((r) => (
                    <tr key={r.symbol} style={{ cursor: 'pointer' }} onClick={() => void loadFnoSymbol(r.symbol)}>
                      <td>{r.symbol}</td>
                      <td>
                        <span className="pill valid">{r.is_index ? 'INDEX' : 'EQ'}</span>
                      </td>
                      <td>{r.price != null ? inrDec(r.price) : '—'}</td>
                      <td className={(r.change_pct || 0) >= 0 ? 'pos' : 'neg'}>
                        {r.change_pct != null ? pct(r.change_pct) : '—'}
                      </td>
                      <td>
                        <button
                          className="btn"
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation()
                            void loadFnoSymbol(r.symbol)
                          }}
                        >
                          F&amp;O Chain
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {!searchBusy && !searchError && searchResults.length === 0 && (
              <p className="empty">No symbols matched. Try NIFTY or RELIANCE.</p>
            )}
          </section>

          {fnoOverview ? (
            <section className="panel" style={{ marginBottom: '1rem' }}>
              <div className="panel-head">
                <h2>
                  {fnoOverview.symbol} · Spot {inrDec(fnoOverview.spot)}
                </h2>
                <span className={`pill ${(fnoOverview.change_pct || 0) >= 0 ? 'buy' : 'sell'}`}>
                  {pct(fnoOverview.change_pct || 0)}
                </span>
              </div>
              <div className="rec-grid">
                <div className="rec-cell">
                  <span>Futures LTP</span>
                  <strong>{inrDec(fnoOverview.futures?.ltp ?? 0)}</strong>
                </div>
                <div className="rec-cell">
                  <span>Basis</span>
                  <strong>
                    {inrDec(fnoOverview.futures?.basis ?? 0)} ({fnoOverview.futures?.basis_pct ?? 0}%)
                  </strong>
                </div>
                <div className="rec-cell">
                  <span>Fut lot</span>
                  <strong>{fnoOverview.futures?.lot_size ?? '—'}</strong>
                </div>
                <div className="rec-cell">
                  <span>ATR</span>
                  <strong>{inrDec(fnoOverview.atr)}</strong>
                </div>
              </div>

              <div className="panel-head" style={{ marginTop: '1rem' }}>
                <h2 style={{ fontSize: '1rem' }}>Expiry Dates</h2>
              </div>
              <div className="chips" style={{ marginBottom: '0.75rem' }}>
                {(fnoOverview.expiries || []).map((ex) => (
                  <button
                    key={ex.expiry}
                    type="button"
                    className="chip"
                    style={{
                      cursor: 'pointer',
                      border: 'none',
                      background: selectedExpiry === ex.expiry ? 'var(--teal-dim)' : undefined,
                      color: selectedExpiry === ex.expiry ? 'var(--teal)' : undefined,
                    }}
                    onClick={() => void changeExpiry(ex.expiry)}
                  >
                    {ex.label} · {ex.kind} · {ex.days_to_expiry}d
                  </button>
                ))}
              </div>
              {(fnoOverview.expiries || []).length === 0 && (
                <p className="empty">No expiry dates returned for this symbol.</p>
              )}

              {optionChain ? (
                <>
                  <div className="rec-grid" style={{ marginBottom: '0.75rem' }}>
                    <div className="rec-cell">
                      <span>Expiry</span>
                      <strong>{optionChain.expiry_label}</strong>
                    </div>
                    <div className="rec-cell">
                      <span>ATM</span>
                      <strong>{optionChain.atm_strike}</strong>
                    </div>
                    <div className="rec-cell">
                      <span>PCR</span>
                      <strong>{optionChain.pcr ?? '—'}</strong>
                    </div>
                    <div className="rec-cell">
                      <span>Max Pain</span>
                      <strong>{optionChain.max_pain}</strong>
                    </div>
                    <div className="rec-cell">
                      <span>IV ATM</span>
                      <strong>{optionChain.iv_atm_pct}%</strong>
                    </div>
                    <div className="rec-cell">
                      <span>Opt lot</span>
                      <strong>{optionChain.lot_size}</strong>
                    </div>
                  </div>
                  <p className="empty" style={{ marginBottom: '0.5rem' }}>
                    {optionChain.note}
                  </p>
                  <div style={{ overflowX: 'auto' }}>
                    <table className="table option-chain">
                      <thead>
                        <tr>
                          <th colSpan={4} style={{ textAlign: 'center', color: 'var(--teal)' }}>
                            CALL (CE)
                          </th>
                          <th style={{ textAlign: 'center' }}>Strike</th>
                          <th colSpan={4} style={{ textAlign: 'center', color: 'var(--amber)' }}>
                            PUT (PE)
                          </th>
                        </tr>
                        <tr>
                          <th>OI</th>
                          <th>Vol</th>
                          <th>IV%</th>
                          <th>LTP</th>
                          <th>Strike</th>
                          <th>LTP</th>
                          <th>IV%</th>
                          <th>Vol</th>
                          <th>OI</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(optionChain.rows || []).map((row) => (
                          <tr
                            key={row.strike}
                            style={{
                              background: row.is_atm ? 'rgba(62, 207, 172, 0.08)' : undefined,
                            }}
                          >
                            <td>{(row.call?.oi ?? 0).toLocaleString('en-IN')}</td>
                            <td>{(row.call?.volume ?? 0).toLocaleString('en-IN')}</td>
                            <td>{(row.call?.iv ?? 0).toFixed(1)}</td>
                            <td className="pos">{inrDec(row.call?.ltp ?? 0)}</td>
                            <td>
                              <strong>
                                {row.strike}
                                {row.is_atm ? ' · ATM' : ''}
                              </strong>
                            </td>
                            <td className="neg">{inrDec(row.put?.ltp ?? 0)}</td>
                            <td>{(row.put?.iv ?? 0).toFixed(1)}</td>
                            <td>{(row.put?.volume ?? 0).toLocaleString('en-IN')}</td>
                            <td>{(row.put?.oi ?? 0).toLocaleString('en-IN')}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {(optionChain.rows || []).length === 0 && (
                    <p className="empty">No CE/PE rows for this expiry.</p>
                  )}
                </>
              ) : (
                <p className="empty">
                  {searchBusy ? 'Loading Call / Put option chain…' : 'Select an expiry above to load CE / PE.'}
                </p>
              )}
            </section>
          ) : (
            !searchBusy &&
            !searchError && (
              <section className="panel">
                <p className="empty">
                  No F&amp;O details yet. Click <strong>NIFTY</strong> or press Search to load expiry dates and the option chain.
                </p>
                <button className="btn primary" type="button" onClick={() => void loadFnoSymbol('NIFTY')}>
                  Load NIFTY F&amp;O Chain
                </button>
              </section>
            )
          )}
        </div>
      )}

      {tab === 'margin' && (
        <div className="margin-page search-fno-page">
          <section className="panel" style={{ marginBottom: '1rem' }}>
            <div className="panel-head">
              <h2>F&amp;O Desk</h2>
              <div className="actions">
                <button className="btn" type="button" onClick={() => void refresh()}>
                  Refresh
                </button>
              </div>
            </div>
            {!marginBook ? (
              <p className="empty">Loading F&amp;O margin…</p>
            ) : (
              <>
                <div className="rec-grid" style={{ marginBottom: '1rem' }}>
                  <div className="rec-cell">
                    <span>Capital</span>
                    <strong>{inr(marginBook.capital)}</strong>
                  </div>
                  <div className="rec-cell">
                    <span>F&amp;O Margin</span>
                    <strong>{inr(marginBook.fo_total_margin)}</strong>
                  </div>
                  <div className="rec-cell">
                    <span>F&amp;O Exposure</span>
                    <strong>{inr(marginBook.fo_total_exposure)}</strong>
                  </div>
                  <div className="rec-cell">
                    <span>Futures Margin</span>
                    <strong>{inr(marginBook.by_segment.futures.margin_used)}</strong>
                  </div>
                  <div className="rec-cell">
                    <span>Options Margin</span>
                    <strong>{inr(marginBook.by_segment.options.margin_used)}</strong>
                  </div>
                  <div className="rec-cell">
                    <span>Available</span>
                    <strong className="pos">{inr(marginBook.available_margin)}</strong>
                  </div>
                  <div className="rec-cell">
                    <span>Open F&amp;O</span>
                    <strong>{marginBook.fno_count}</strong>
                  </div>
                  <div className="rec-cell">
                    <span>Unrealized</span>
                    <strong className={marginBook.unrealized_pnl >= 0 ? 'pos' : 'neg'}>
                      {inr(marginBook.unrealized_pnl)}
                    </strong>
                  </div>
                </div>
                <p className="empty">
                  Paper F&amp;O margins are SPAN-style estimates. Open futures/options show expiry and CE/PE below; pick a
                  symbol to inspect the full chain.
                </p>
              </>
            )}
          </section>

          <section className="panel" style={{ marginBottom: '1rem' }}>
            <div className="panel-head">
              <h2>Open F&amp;O Positions</h2>
              <span className="pill buy">{marginBook?.fno_count ?? 0} open</span>
            </div>
            {!marginBook || marginBook.fno.length === 0 ? (
              <p className="empty">
                No open Futures / Options yet. Start paper with F&amp;O enabled, or use Overview → Scan (Futures / Options).
                Expiry dates and Call/Put chain are available in the section below.
              </p>
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table className="table">
                  <thead>
                    <tr>
                      <th>Symbol</th>
                      <th>Type</th>
                      <th>CE/PE</th>
                      <th>Strike</th>
                      <th>Expiry</th>
                      <th>Side</th>
                      <th>Lots</th>
                      <th>Mult</th>
                      <th>LTP</th>
                      <th>Margin</th>
                      <th>PnL</th>
                    </tr>
                  </thead>
                  <tbody>
                    {marginBook.fno.map((p) => (
                      <tr
                        key={`fo-${p.position_id}-${p.symbol}-${p.product}`}
                        style={{ cursor: 'pointer' }}
                        onClick={() => {
                          setSearchQuery(p.symbol)
                          void loadFnoSymbol(p.symbol, p.expiry || undefined)
                        }}
                      >
                        <td>{p.symbol}</td>
                        <td>
                          {p.product} · {p.trade_type}
                        </td>
                        <td>
                          <span className={`pill ${p.option_type === 'PE' ? 'sell' : 'buy'}`}>
                            {p.option_type || (p.product === 'Futures' ? 'FUT' : '—')}
                          </span>
                        </td>
                        <td>{p.strike != null ? p.strike : '—'}</td>
                        <td>{p.expiry_label || p.expiry || '—'}</td>
                        <td>
                          <span className={`pill ${p.side === 'BUY' ? 'buy' : 'sell'}`}>{p.side}</span>
                        </td>
                        <td>{p.quantity}</td>
                        <td>{p.multiplier}</td>
                        <td>{inrDec(p.ltp)}</td>
                        <td>
                          {inr(p.margin_required)}
                          <span className="muted"> ({p.margin_pct}%)</span>
                        </td>
                        <td className={p.unrealized_pnl >= 0 ? 'pos' : 'neg'}>{inr(p.unrealized_pnl)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="panel" style={{ marginBottom: '1rem' }}>
            <div className="panel-head">
              <h2>Expiry Dates · Call (CE) / Put (PE)</h2>
              <div className="actions">
                <button
                  className="btn primary"
                  type="button"
                  disabled={searchBusy}
                  onClick={() => void loadFnoSymbol(searchSymbol || 'NIFTY')}
                >
                  {searchBusy ? 'Loading…' : 'Load Chain'}
                </button>
              </div>
            </div>
            <p className="empty" style={{ marginBottom: '0.75rem' }}>
              Select an underlying to view futures basis, expiry chips, and the full CE/PE option chain.
            </p>
            <div className="chips" style={{ marginBottom: '0.75rem' }}>
              {['NIFTY', 'BANKNIFTY', 'RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'SBIN', 'ICICIBANK'].map((s) => (
                <button
                  key={s}
                  type="button"
                  className="chip"
                  style={{
                    cursor: 'pointer',
                    border: 'none',
                    background: searchSymbol === s ? 'var(--teal-dim)' : undefined,
                    color: searchSymbol === s ? 'var(--teal)' : undefined,
                  }}
                  onClick={() => {
                    setSearchQuery(s)
                    void loadFnoSymbol(s)
                  }}
                >
                  {s}
                </button>
              ))}
            </div>
            {searchError && (
              <div className="emergency-banner" style={{ marginBottom: '0.75rem' }}>
                {searchError}{' '}
                <button className="btn" type="button" onClick={() => void loadFnoSymbol(searchSymbol || 'NIFTY')}>
                  Retry
                </button>
              </div>
            )}
            {searchBusy && !fnoOverview && <p className="empty">Loading F&amp;O chain for {searchSymbol || 'NIFTY'}…</p>}

            {fnoOverview && (
              <>
                <div className="panel-head">
                  <h2 style={{ fontSize: '1rem' }}>
                    {fnoOverview.symbol} · Spot {inrDec(fnoOverview.spot)}
                  </h2>
                  <span className={`pill ${(fnoOverview.change_pct || 0) >= 0 ? 'buy' : 'sell'}`}>
                    {pct(fnoOverview.change_pct || 0)}
                  </span>
                </div>
                <div className="rec-grid" style={{ marginBottom: '0.75rem' }}>
                  <div className="rec-cell">
                    <span>Futures LTP</span>
                    <strong>{inrDec(fnoOverview.futures?.ltp ?? 0)}</strong>
                  </div>
                  <div className="rec-cell">
                    <span>Basis</span>
                    <strong>
                      {inrDec(fnoOverview.futures?.basis ?? 0)} ({fnoOverview.futures?.basis_pct ?? 0}%)
                    </strong>
                  </div>
                  <div className="rec-cell">
                    <span>Fut lot</span>
                    <strong>{fnoOverview.futures?.lot_size ?? '—'}</strong>
                  </div>
                  <div className="rec-cell">
                    <span>ATR</span>
                    <strong>{inrDec(fnoOverview.atr)}</strong>
                  </div>
                </div>

                <div className="panel-head" style={{ marginTop: '0.5rem' }}>
                  <h2 style={{ fontSize: '1rem' }}>Expiry Dates</h2>
                </div>
                <div className="chips" style={{ marginBottom: '0.75rem' }}>
                  {(fnoOverview.expiries || []).map((ex) => (
                    <button
                      key={ex.expiry}
                      type="button"
                      className="chip"
                      style={{
                        cursor: 'pointer',
                        border: 'none',
                        background: selectedExpiry === ex.expiry ? 'var(--teal-dim)' : undefined,
                        color: selectedExpiry === ex.expiry ? 'var(--teal)' : undefined,
                      }}
                      onClick={() => void changeExpiry(ex.expiry)}
                    >
                      {ex.label} · {ex.kind} · {ex.days_to_expiry}d
                    </button>
                  ))}
                </div>

                {optionChain ? (
                  <>
                    <div className="rec-grid" style={{ marginBottom: '0.75rem' }}>
                      <div className="rec-cell">
                        <span>Expiry</span>
                        <strong>{optionChain.expiry_label}</strong>
                      </div>
                      <div className="rec-cell">
                        <span>ATM</span>
                        <strong>{optionChain.atm_strike}</strong>
                      </div>
                      <div className="rec-cell">
                        <span>PCR</span>
                        <strong>{optionChain.pcr ?? '—'}</strong>
                      </div>
                      <div className="rec-cell">
                        <span>Max Pain</span>
                        <strong>{optionChain.max_pain}</strong>
                      </div>
                      <div className="rec-cell">
                        <span>IV ATM</span>
                        <strong>{optionChain.iv_atm_pct}%</strong>
                      </div>
                      <div className="rec-cell">
                        <span>Opt lot</span>
                        <strong>{optionChain.lot_size}</strong>
                      </div>
                    </div>
                    <p className="empty" style={{ marginBottom: '0.5rem' }}>
                      {optionChain.note}
                    </p>
                    <div style={{ overflowX: 'auto' }}>
                      <table className="table option-chain">
                        <thead>
                          <tr>
                            <th colSpan={4} style={{ textAlign: 'center', color: 'var(--teal)' }}>
                              CALL (CE)
                            </th>
                            <th style={{ textAlign: 'center' }}>Strike</th>
                            <th colSpan={4} style={{ textAlign: 'center', color: 'var(--amber)' }}>
                              PUT (PE)
                            </th>
                          </tr>
                          <tr>
                            <th>OI</th>
                            <th>Vol</th>
                            <th>IV%</th>
                            <th>LTP</th>
                            <th>Strike</th>
                            <th>LTP</th>
                            <th>IV%</th>
                            <th>Vol</th>
                            <th>OI</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(optionChain.rows || []).map((row) => (
                            <tr
                              key={row.strike}
                              style={{
                                background: row.is_atm ? 'rgba(62, 207, 172, 0.08)' : undefined,
                              }}
                            >
                              <td>{(row.call?.oi ?? 0).toLocaleString('en-IN')}</td>
                              <td>{(row.call?.volume ?? 0).toLocaleString('en-IN')}</td>
                              <td>{(row.call?.iv ?? 0).toFixed(1)}</td>
                              <td className="pos">{inrDec(row.call?.ltp ?? 0)}</td>
                              <td>
                                <strong>
                                  {row.strike}
                                  {row.is_atm ? ' · ATM' : ''}
                                </strong>
                              </td>
                              <td className="neg">{inrDec(row.put?.ltp ?? 0)}</td>
                              <td>{(row.put?.iv ?? 0).toFixed(1)}</td>
                              <td>{(row.put?.volume ?? 0).toLocaleString('en-IN')}</td>
                              <td>{(row.put?.oi ?? 0).toLocaleString('en-IN')}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                ) : (
                  <p className="empty">{searchBusy ? 'Loading CE/PE chain…' : 'Select an expiry to load Call / Put.'}</p>
                )}
              </>
            )}
          </section>
        </div>
      )}

      {tab === 'chat' && (
        <section className="panel" style={{ marginBottom: '1rem' }}>
          <div className="panel-head">
            <h2>LLM Coach</h2>
            <div className="actions">
              <span className={`pill ${llmCfg?.has_api_key ? 'valid' : 'invalid'}`}>
                {llmCfg?.has_api_key ? `LLM · ${llmCfg.provider}` : 'LLM OFF'}
              </span>
              <button className="btn" type="button" onClick={() => setShowLlmSettings((v) => !v)}>
                LLM settings
              </button>
              <button className="btn warn" type="button" disabled={chatBusy} onClick={analyzeLosses}>
                Analyze losses
              </button>
            </div>
          </div>

          {showLlmSettings && (
            <div className="rec-detail" style={{ marginBottom: '0.85rem' }}>
              <p className="prose">
                {llmCfg?.hint || 'Add a provider API key to enable LLM post-mortems on losses and strategy gaps.'}
                {llmCfg?.has_api_key ? ` Key source: ${llmCfg.mode === 'llm' ? 'configured' : llmCfg.mode}.` : ''}
              </p>
              <div className="scan-row">
                <select
                  value={llmProvider}
                  onChange={(e) => setLlmProvider(e.target.value)}
                  style={{
                    background: 'var(--bg-0)',
                    color: 'var(--text)',
                    border: '1px solid var(--line)',
                    borderRadius: 8,
                    padding: '0.55rem 0.75rem',
                  }}
                >
                  <option value="groq">Groq (recommended)</option>
                  <option value="openai">OpenAI</option>
                  <option value="openrouter">OpenRouter</option>
                </select>
                <input
                  type="password"
                  value={llmKey}
                  onChange={(e) => setLlmKey(e.target.value)}
                  placeholder={llmCfg?.has_api_key ? '•••• key saved — paste to replace' : 'Paste API key'}
                />
                <input
                  value={llmModel}
                  onChange={(e) => setLlmModel(e.target.value)}
                  placeholder="Model (optional)"
                />
                <button className="btn primary" type="button" disabled={chatBusy} onClick={saveLlm}>
                  Save
                </button>
              </div>
            </div>
          )}

          <div className="chat-hints">
            {[
              'Why did we lose money?',
              'What did the strategy miss?',
              'Which filters failed on losing trades?',
              'How should we improve entry logic?',
              'Was risk management followed?',
            ].map((q) => (
              <button key={q} type="button" disabled={chatBusy} onClick={() => sendChat(q, 'loss_review')}>
                {q}
              </button>
            ))}
          </div>
          <div className="chat-shell">
            <div className="chat-log">
              {chatMessages.map((m, i) => (
                <div key={`${m.role}-${i}`} className={`chat-bubble ${m.role}`}>
                  <span className="who">
                    {m.role === 'user' ? 'You' : 'QuantX LLM'}
                    {m.mode ? ` · ${m.mode}` : ''}
                  </span>
                  {m.content}
                </div>
              ))}
            </div>
            <div className="chat-input-row">
              <textarea
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                placeholder="Ask for loss reasons, missed signals, strategy gaps…"
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    sendChat()
                  }
                }}
              />
              <button className="btn primary" disabled={chatBusy || !chatInput.trim()} onClick={() => sendChat()}>
                {chatBusy ? '…' : 'Send'}
              </button>
            </div>
          </div>
        </section>
      )}

      {tab === 'orders' && (
        <section className="panel" style={{ marginBottom: '1rem' }}>
          <div className="panel-head">
            <h2>Orders</h2>
            <div className="actions">
              <button className="btn" disabled={busy} onClick={refresh}>
                Refresh
              </button>
            </div>
          </div>
          {orders.length === 0 ? (
            <p className="empty">No orders yet. Start Auto or Run Cycle to generate paper fills.</p>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th>Qty</th>
                  <th>Price</th>
                  <th>Status</th>
                  <th>Message</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o) => (
                  <tr key={o.id}>
                    <td style={{ whiteSpace: 'nowrap' }}>{(o.created_at || '').replace('T', ' ').slice(0, 19)}</td>
                    <td>{o.symbol}</td>
                    <td>
                      <span className={`pill ${o.side === 'BUY' ? 'buy' : 'sell'}`}>{o.side}</span>
                    </td>
                    <td>{o.quantity}</td>
                    <td>{o.price?.toFixed?.(2) ?? o.price}</td>
                    <td>
                      <span className={`pill ${o.status === 'FILLED' ? 'valid' : 'invalid'}`}>{o.status}</span>
                    </td>
                    <td style={{ fontFamily: 'var(--font)', color: 'var(--muted)', maxWidth: 280 }}>{o.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {tab === 'pnl' && (
        <div className="stack" style={{ marginBottom: '1rem' }}>
          <section className="panel">
            <div className="panel-head">
              <h2>P&amp;L Summary</h2>
              <div className="actions">
                <button className="btn" disabled={busy} onClick={refresh}>
                  Refresh MTM
                </button>
              </div>
            </div>
            {!pnlData ? (
              <p className="empty">Loading P&amp;L…</p>
            ) : (
              <>
                <div className="rec-grid">
                  <div className="rec-cell">
                    <span>Total P&amp;L</span>
                    <strong className={pnlData.summary.total_pnl >= 0 ? 'pos' : 'neg'}>
                      {inr(pnlData.summary.total_pnl)}
                    </strong>
                  </div>
                  <div className="rec-cell">
                    <span>Unrealized</span>
                    <strong className={pnlData.summary.unrealized_pnl >= 0 ? 'pos' : 'neg'}>
                      {inr(pnlData.summary.unrealized_pnl)}
                    </strong>
                  </div>
                  <div className="rec-cell">
                    <span>Realized (closed)</span>
                    <strong className={pnlData.summary.closed_realized_pnl >= 0 ? 'pos' : 'neg'}>
                      {inr(pnlData.summary.closed_realized_pnl)}
                    </strong>
                  </div>
                  <div className="rec-cell">
                    <span>Fees</span>
                    <strong className="warn">{inr(pnlData.summary.total_fees)}</strong>
                  </div>
                  <div className="rec-cell">
                    <span>Capital</span>
                    <strong>{inr(pnlData.summary.capital)}</strong>
                  </div>
                  <div className="rec-cell">
                    <span>Drawdown</span>
                    <strong className={pnlData.summary.drawdown_pct > 2 ? 'warn' : ''}>
                      {pnlData.summary.drawdown_pct.toFixed(2)}%
                    </strong>
                  </div>
                  <div className="rec-cell">
                    <span>Win rate</span>
                    <strong>
                      {pnlData.summary.win_rate.toFixed(1)}% ({pnlData.summary.wins}W / {pnlData.summary.losses}L)
                    </strong>
                  </div>
                  <div className="rec-cell">
                    <span>Open / Closed</span>
                    <strong>
                      {pnlData.summary.open_positions} / {pnlData.summary.closed_trades}
                    </strong>
                  </div>
                </div>
              </>
            )}
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>Order-level P&amp;L</h2>
            </div>
            {!pnlData || pnlData.orders.length === 0 ? (
              <p className="empty">No filled orders with P&amp;L yet.</p>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Symbol</th>
                    <th>Side</th>
                    <th>Qty</th>
                    <th>Entry</th>
                    <th>LTP / Exit</th>
                    <th>SL</th>
                    <th>T1</th>
                    <th>Pos</th>
                    <th>P&amp;L</th>
                    <th>%</th>
                    <th>Exit reason</th>
                  </tr>
                </thead>
                <tbody>
                  {pnlData.orders
                    .filter((o) => o.status === 'FILLED')
                    .map((o) => {
                      const pnlVal = o.pnl ?? 0
                      const mark = o.exit_price ?? o.entry_price
                      return (
                        <tr key={`pnl-ord-${o.id}`}>
                          <td style={{ whiteSpace: 'nowrap' }}>{(o.created_at || '').replace('T', ' ').slice(0, 19)}</td>
                          <td>{o.symbol}</td>
                          <td>
                            <span className={`pill ${o.side === 'BUY' ? 'buy' : 'sell'}`}>{o.side}</span>
                          </td>
                          <td>{o.quantity}</td>
                          <td>{Number(o.entry_price ?? o.price).toFixed(2)}</td>
                          <td>{mark != null ? Number(mark).toFixed(2) : '—'}</td>
                          <td>{o.stop_loss != null ? Number(o.stop_loss).toFixed(2) : '—'}</td>
                          <td>{o.target_1 != null ? Number(o.target_1).toFixed(2) : '—'}</td>
                          <td>
                            <span className={`pill ${o.position_status === 'OPEN' ? 'valid' : 'invalid'}`}>
                              {o.position_status || '—'}
                            </span>
                          </td>
                          <td className={pnlVal >= 0 ? 'pos' : 'neg'}>{inr(pnlVal)}</td>
                          <td className={(o.pnl_pct ?? 0) >= 0 ? 'pos' : 'neg'}>
                            {o.pnl_pct != null ? pct(o.pnl_pct) : '—'}
                          </td>
                          <td style={{ fontFamily: 'var(--font)', color: 'var(--muted)' }}>
                            {o.exit_reason || (o.position_status === 'OPEN' ? 'Open — unrealized' : '—')}
                          </td>
                        </tr>
                      )
                    })}
                </tbody>
              </table>
            )}
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>Open positions (unrealized)</h2>
            </div>
            {!pnlData || pnlData.open_positions.length === 0 ? (
              <p className="empty">No open positions.</p>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Side</th>
                    <th>Qty</th>
                    <th>Entry</th>
                    <th>LTP</th>
                    <th>SL</th>
                    <th>T1 / T2</th>
                    <th>Unrealized</th>
                    <th>%</th>
                  </tr>
                </thead>
                <tbody>
                  {pnlData.open_positions.map((p) => (
                    <tr key={`open-${p.id}`}>
                      <td>{p.symbol}</td>
                      <td>
                        <span className={`pill ${p.side === 'BUY' ? 'buy' : 'sell'}`}>{p.side}</span>
                      </td>
                      <td>{p.quantity}</td>
                      <td>{p.entry_price.toFixed(2)}</td>
                      <td>{p.current_price.toFixed(2)}</td>
                      <td>{p.stop_loss.toFixed(2)}</td>
                      <td>
                        {p.target_1.toFixed(2)} / {p.target_2.toFixed(2)}
                      </td>
                      <td className={p.pnl >= 0 ? 'pos' : 'neg'}>{inr(p.pnl)}</td>
                      <td className={p.pnl_pct >= 0 ? 'pos' : 'neg'}>{pct(p.pnl_pct)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>Closed trades (realized)</h2>
            </div>
            {!pnlData || pnlData.closed_positions.length === 0 ? (
              <p className="empty">No closed trades yet.</p>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Side</th>
                    <th>Qty</th>
                    <th>Entry</th>
                    <th>Exit</th>
                    <th>Realized P&amp;L</th>
                    <th>%</th>
                    <th>Reason</th>
                    <th>Closed</th>
                  </tr>
                </thead>
                <tbody>
                  {pnlData.closed_positions.map((p) => (
                    <tr key={`closed-${p.id}`}>
                      <td>{p.symbol}</td>
                      <td>
                        <span className={`pill ${p.side === 'BUY' ? 'buy' : 'sell'}`}>{p.side}</span>
                      </td>
                      <td>{p.quantity}</td>
                      <td>{p.entry_price.toFixed(2)}</td>
                      <td>{p.exit_price != null ? Number(p.exit_price).toFixed(2) : '—'}</td>
                      <td className={p.pnl >= 0 ? 'pos' : 'neg'}>{inr(p.pnl)}</td>
                      <td className={p.pnl_pct >= 0 ? 'pos' : 'neg'}>{pct(p.pnl_pct)}</td>
                      <td style={{ fontFamily: 'var(--font)', color: 'var(--muted)' }}>{p.exit_reason || '—'}</td>
                      <td style={{ whiteSpace: 'nowrap' }}>
                        {p.closed_at ? String(p.closed_at).replace('T', ' ').slice(0, 19) : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </div>
      )}

      {tab === 'cycles' && (
        <section className="panel" style={{ marginBottom: '1rem' }}>
          <div className="panel-head">
            <h2>Paper Cycles</h2>
            <div className="actions">
              <button className="btn" disabled={busy} onClick={refresh}>
                Refresh
              </button>
              <button className="btn" disabled={busy} onClick={runPaperCycle}>
                Run Cycle
              </button>
            </div>
          </div>
          <p className="prose" style={{ marginBottom: '0.75rem' }}>
            Session {paperRunning ? 'RUNNING' : 'IDLE'} · total cycles {paperStats.cycles} · executed {paperStats.executed} ·
            rejected {paperStats.rejected} · skipped {paperStats.skipped || 0}. Last: {paperMsg}
          </p>
          {cycles.length === 0 ? (
            <p className="empty">No cycle history yet. Click Start Auto or Run Cycle.</p>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Time</th>
                  <th>Valid</th>
                  <th>Filled</th>
                  <th>Rejected</th>
                  <th>Skipped</th>
                  <th>MTM closed</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {cycles.map((c) => (
                  <tr key={c.id}>
                    <td>{c.cycle_no}</td>
                    <td style={{ whiteSpace: 'nowrap' }}>{(c.created_at || '').replace('T', ' ').slice(0, 19)}</td>
                    <td>
                      {c.valid_count}/{c.scanned}
                    </td>
                    <td className="pos">{c.executed_count}</td>
                    <td className="warn">{c.rejected_count}</td>
                    <td style={{ color: 'var(--muted)' }}>{c.skipped_count ?? c.skipped?.length ?? 0}</td>
                    <td>{c.mtm_closed}</td>
                    <td style={{ fontFamily: 'var(--font)', color: 'var(--muted)' }}>
                      <div>{c.message}</div>
                      {c.executed?.length > 0 && (
                        <div className="pos" style={{ fontSize: '0.75rem', marginTop: 4 }}>
                          Filled:{' '}
                          {c.executed
                            .map((x) => `${x.symbol} ${x.side || ''} ${x.quantity || ''}@${x.fill_price ?? ''}`)
                            .join(' · ')}
                        </div>
                      )}
                      {c.rejected?.length > 0 && (
                        <div className="warn" style={{ fontSize: '0.75rem', marginTop: 2 }}>
                          Rejected: {c.rejected.map((x) => `${x.symbol} (${x.reason})`).join(' · ')}
                        </div>
                      )}
                      {(c.skipped?.length || 0) > 0 && (
                        <div style={{ fontSize: '0.75rem', marginTop: 2, color: 'var(--muted)' }}>
                          Skipped (capacity):{' '}
                          {c.skipped!.map((x) => `${x.symbol} ${x.trade_type || ''} (${x.reason})`).join(' · ')}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {tab === 'overview' && (
      <div className="grid">
        <div className="stack">
          <section className="panel">
            <div className="panel-head">
              <h2>Signal Scanner</h2>
              <div className="actions">
                <button className="btn primary" disabled={busy} onClick={runScan}>
                  {huntMarket ? 'Hunt Market' : 'Scan'}
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
                placeholder={huntMarket ? 'Optional seeds (AI hunts market, then fills winners here)' : 'NSE symbols, comma-separated'}
              />
            </div>
            <div className="scan-row" style={{ marginTop: '0.5rem', gap: '0.75rem', alignItems: 'center' }}>
              <label className="chip" style={{ cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={huntMarket}
                  onChange={(e) => setHuntMarket(e.target.checked)}
                  style={{ marginRight: '0.4rem' }}
                />
                AI market hunt (discover symbols → strategies / styles)
              </label>
              <label className="chip" style={{ cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={scanProduct === 'ALL' || enableFno}
                  onChange={(e) => {
                    const on = e.target.checked
                    setEnableFno(on)
                    setScanProduct(on ? 'ALL' : 'SWING')
                  }}
                  style={{ marginRight: '0.4rem' }}
                />
                Include F&amp;O (Futures + Options)
              </label>
              <select
                value={scanProduct}
                onChange={(e) => {
                  const v = e.target.value as typeof scanProduct
                  setScanProduct(v)
                  setEnableFno(v === 'ALL' || v === 'FUTURES' || v === 'OPTIONS')
                }}
                aria-label="Product"
              >
                <option value="ALL">All products</option>
                <option value="SWING">Stocks only</option>
                <option value="FUTURES">Futures only</option>
                <option value="OPTIONS">Options only</option>
              </select>
              <select
                value={scanStyle}
                onChange={(e) => setScanStyle(e.target.value)}
                aria-label="Trading style"
                title="Trading style (Scalping → ETF investing)"
              >
                <option value="auto">Style: Auto (regime)</option>
                <option value="all">Style: All styles</option>
                {styleGroups.map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name}
                  </option>
                ))}
              </select>
            </div>
            {huntMeta && <p className="prose" style={{ marginTop: '0.5rem' }}>{huntMeta}</p>}
            {hunted.length > 0 && (
              <div style={{ overflowX: 'auto', marginTop: '0.75rem', marginBottom: '0.75rem' }}>
                <table className="table">
                  <thead>
                    <tr>
                      <th>Hunted</th>
                      <th>Score</th>
                      <th>Chg%</th>
                      <th>Vol</th>
                      <th>Why</th>
                    </tr>
                  </thead>
                  <tbody>
                    {hunted.map((h) => (
                      <tr key={h.symbol} onClick={() => void analyzeOne(h.symbol)} style={{ cursor: 'pointer' }}>
                        <td>
                          {h.symbol}
                          {h.is_index ? <span className="pill valid" style={{ marginLeft: 6 }}>IDX</span> : null}
                        </td>
                        <td>{h.score.toFixed(0)}</td>
                        <td className={h.change_pct >= 0 ? 'pos' : 'neg'}>{pct(h.change_pct)}</td>
                        <td>{h.volume_ratio.toFixed(1)}x</td>
                        <td style={{ fontSize: '0.75rem', color: 'var(--muted)' }}>{(h.reasons || []).join(' · ')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {busy && recs.length === 0 ? (
              <p className="empty">{huntMarket ? 'Hunting market & processing strategies…' : 'Scanning…'}</p>
            ) : recs.length === 0 ? (
              <p className="empty">
                {huntMarket
                  ? 'Click Hunt Market — AI screens the NSE universe, brings opportunity symbols here, then runs strategies / trading models.'
                  : 'Run a scan to generate Stocks and F&O recommendations with confidence & risk scores.'}
              </p>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Product</th>
                    <th>Side</th>
                    <th>Conf</th>
                    <th>RR</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {recs.map((r) => (
                    <tr
                      key={`${r.symbol}-${r.trade_type}-${r.generated_at}`}
                      onClick={() => setSelected(r)}
                      style={{
                        cursor: 'pointer',
                        outline:
                          selected?.symbol === r.symbol && selected?.trade_type === r.trade_type
                            ? '1px solid rgba(62,207,172,0.35)'
                            : undefined,
                      }}
                    >
                      <td>{r.symbol}</td>
                      <td>
                        <span className="pill valid">{r.trade_type}</span>
                        {(() => {
                          const sid = (r.supporting_indicators || [])
                            .find((x) => x.startsWith('style:'))
                            ?.slice(6)
                          if (!sid) return null
                          const name = styleGroups.find((g) => g.id === sid)?.name || sid
                          return (
                            <div style={{ fontSize: '0.7rem', color: 'var(--muted)', marginTop: 2 }}>
                              {name}
                            </div>
                          )
                        })()}
                      </td>
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
                <div className="panel-head" style={{ marginBottom: '0.75rem' }}>
                  <h2 style={{ fontSize: '1.05rem' }}>
                    {selected.symbol} · {selected.side} · {selected.trade_type}
                  </h2>
                  <span className={`pill ${selected.valid ? 'valid' : 'invalid'}`}>
                    {selected.valid ? 'VALID' : 'REJECTED'}
                  </span>
                </div>
                {!selected.valid && (
                  <div className="emergency-banner" style={{ marginBottom: '0.75rem' }}>
                    Rejected: {selected.rejection_reason || selected.reason || 'No trade setup'}
                  </div>
                )}
                <div className="rec-grid">
                  <div className="rec-cell">
                    <span>Product</span>
                    <strong>{selected.trade_type}</strong>
                  </div>
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
              <div className="rec-cell">
                <span>Skipped (slots)</span>
                <strong style={{ color: 'var(--muted)' }}>{paperStats.skipped || 0}</strong>
              </div>
            </div>
            <label className="chip" style={{ display: 'inline-flex', marginTop: '0.75rem', cursor: paperRunning ? 'not-allowed' : 'pointer' }}>
              <input
                type="checkbox"
                checked={enableFno}
                disabled={paperRunning}
                onChange={(e) => setEnableFno(e.target.checked)}
                style={{ marginRight: '0.4rem' }}
              />
              Auto-trade F&amp;O with Stocks
            </label>
            <div className="scan-row" style={{ marginTop: '0.65rem' }}>
              <select
                value={scanStyle}
                disabled={paperRunning}
                onChange={(e) => setScanStyle(e.target.value)}
                aria-label="Paper trading style"
                title="Paper agent trading style"
              >
                <option value="auto">Style: Auto (regime)</option>
                <option value="all">Style: All styles (Scalping→ETF)</option>
                {styleGroups.map((g) => (
                  <option key={`paper-${g.id}`} value={g.id}>
                    {g.name}
                  </option>
                ))}
              </select>
            </div>
            <p className="prose" style={{ marginTop: '0.4rem', fontSize: '0.8rem' }}>
              Agent can run Scalping, Intraday, Momentum, Swing, Position, Trend, Breakout, Mean Reversion,
              Options/Futures, Arbitrage, Pair, Event/News, Quant/Algo, and long-term investing styles when selected.
            </p>

            <div className="panel-head" style={{ marginTop: '1rem' }}>
              <h2 style={{ fontSize: '1rem' }}>Paper Capital</h2>
            </div>
            <div className="rec-grid">
              <div className="rec-cell">
                <span>Current</span>
                <strong>{portfolio ? inr(portfolio.capital) : '—'}</strong>
              </div>
              <div className="rec-cell">
                <span>Strategies loaded</span>
                <strong>{strategyCount || '—'}</strong>
              </div>
              <div className="rec-cell">
                <span>Regime</span>
                <strong>{regimeInfo?.regime || '—'}</strong>
              </div>
              <div className="rec-cell">
                <span>Regime conf</span>
                <strong>{regimeInfo ? `${regimeInfo.confidence.toFixed(0)}%` : '—'}</strong>
              </div>
            </div>
            <div className="actions" style={{ marginTop: '0.65rem', flexWrap: 'wrap', gap: '0.4rem' }}>
              <button className="btn" type="button" disabled={busy} onClick={() => void adjustCapital(100_000)}>
                + ₹1L
              </button>
              <button className="btn" type="button" disabled={busy} onClick={() => void adjustCapital(500_000)}>
                + ₹5L
              </button>
              <button className="btn" type="button" disabled={busy} onClick={() => void adjustCapital(1_000_000)}>
                + ₹10L
              </button>
              <button className="btn warn" type="button" disabled={busy} onClick={() => void adjustCapital(-100_000)}>
                − ₹1L
              </button>
              <button className="btn warn" type="button" disabled={busy} onClick={() => void adjustCapital(-500_000)}>
                − ₹5L
              </button>
            </div>
            <div className="scan-row" style={{ marginTop: '0.5rem' }}>
              <input
                value={capitalDelta}
                onChange={(e) => setCapitalDelta(e.target.value)}
                placeholder="Set capital (INR)"
              />
              <button className="btn primary" type="button" disabled={busy} onClick={() => void setCapitalAbsolute()}>
                Set Capital
              </button>
            </div>
            {regimeInfo && (
              <p className="prose" style={{ marginTop: '0.75rem' }}>
                <strong>Regime.</strong> {regimeInfo.summary}
              </p>
            )}
            {learningTop.length > 0 && (
              <p className="prose">
                <strong>Self-training top.</strong>{' '}
                {learningTop
                  .map((r) => `${r.strategy} (w=${r.weight.toFixed(2)}, wr=${r.win_rate.toFixed(0)}%)`)
                  .join(' · ')}
              </p>
            )}
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
                    <th>Product</th>
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
                        <span className="pill valid">{p.trade_type || 'SWING'}</span>
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
                  minWidth: '220px',
                }}
              >
                {styleGroups.length > 0 ? (
                  <>
                    {(() => {
                      const inStyles = new Set(styleGroups.flatMap((g) => g.strategies))
                      const other = strategyCatalog.filter((s) => !inStyles.has(s.name)).map((s) => s.name)
                      return (
                        <>
                          {other.length > 0 && (
                            <optgroup label="Core / Additive">
                              {other.map((name) => (
                                <option key={name} value={name}>
                                  {name}
                                </option>
                              ))}
                            </optgroup>
                          )}
                          {styleGroups.map((g) =>
                            g.strategies.length ? (
                              <optgroup key={g.id} label={`Style · ${g.name}`}>
                                {g.strategies.map((name) => (
                                  <option key={`${g.id}-${name}`} value={name}>
                                    {name}
                                  </option>
                                ))}
                              </optgroup>
                            ) : null,
                          )}
                        </>
                      )
                    })()}
                  </>
                ) : strategyCatalog.length > 0 ? (
                  strategyCatalog.map((s) => (
                    <option key={s.name} value={s.name}>
                      {s.name}
                    </option>
                  ))
                ) : (
                  <>
                    <option value="swing_trend">swing_trend</option>
                    <option value="breakout">breakout</option>
                    <option value="intraday_mean_reversion">intraday_mean_reversion</option>
                    <option value="scalping_micro">scalping_micro</option>
                    <option value="futures_trend">futures_trend</option>
                    <option value="options_directional">options_directional</option>
                  </>
                )}
              </select>
            </div>
            {btResult ? <pre className="report-box">{btResult}</pre> : <p className="empty">Run a 1Y backtest — dropdown includes all trading-style strategies (self-train runs on the backend).</p>}
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
      )}

      <p className="status-line">{status}</p>
    </div>
  )
}

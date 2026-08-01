export type AIScores = {
  confidence: number
  risk_score: number
  volatility_score: number
  probability_of_success: number
  expected_return_pct: number
  expected_drawdown_pct: number
}

export type TradeRecommendation = {
  symbol: string
  exchange: string
  market_direction: string
  trade_type: string
  side: string
  entry: number
  stop_loss: number
  target_1: number
  target_2: number
  risk_reward: number
  quantity: number
  capital_at_risk: number
  scores: AIScores
  reason: string
  supporting_indicators: string[]
  fundamental_summary: string
  options_summary: string
  risk_notes: string
  alternative_scenario: string
  valid: boolean
  rejection_reason?: string | null
  generated_at: string
}

export type PortfolioSnapshot = {
  capital: number
  available_margin: number
  used_margin: number
  open_positions: number
  unrealized_pnl: number
  realized_pnl_today: number
  realized_pnl_week: number
  total_pnl: number
  daily_pnl_pct: number
  weekly_pnl_pct: number
  drawdown_pct: number
  consecutive_losses: number
  trading_halted: boolean
  halt_reason?: string | null
  kill_switch_active: boolean
  mode: string
}

export type Position = {
  id: number
  symbol: string
  side: string
  quantity: number
  entry_price: number
  current_price: number
  stop_loss: number
  target_1: number
  target_2: number
  pnl: number
  pnl_pct: number
  status: string
  confidence: number
  reason: string
}

export type RiskStatus = {
  can_trade: boolean
  reasons: string[]
  daily_loss_used_pct: number
  weekly_loss_used_pct: number
  drawdown_pct: number
  open_positions: number
  consecutive_losses: number
  risk_budget_remaining: number
}

export type MarketSession = {
  is_open: boolean
  phase: string
  server_time_ist: string
  next_open?: string | null
}

export type EmergencyState = {
  kill_switch: boolean
  panic_exit_requested: boolean
  max_drawdown_lock: boolean
  manual_override: boolean
  broker_connected: boolean
  internet_ok: boolean
  messages: string[]
}

export type JournalEntry = {
  id: number
  symbol: string
  side: string
  entry: number
  exit: number
  quantity: number
  pnl: number
  pnl_pct: number
  reason: string
  lessons: string
  confidence: number
  closed_at: string
}

export type Quote = {
  symbol: string
  price: number
  change_pct: number
  volume: number
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || res.statusText)
  }
  return res.json()
}

export const api = {
  health: () => req<{ agent: string; version: string; mode: string }>('/api/health'),
  portfolio: () => req<PortfolioSnapshot>('/api/portfolio'),
  risk: () => req<RiskStatus>('/api/risk'),
  session: () => req<MarketSession>('/api/session'),
  positions: () => req<Position[]>('/api/positions'),
  journal: () => req<JournalEntry[]>('/api/journal'),
  watchlist: () => req<Quote[]>('/api/watchlist'),
  macro: () => req<Record<string, number>>('/api/macro'),
  emergency: () => req<EmergencyState>('/api/emergency'),
  analyze: (symbol: string) =>
    req<TradeRecommendation>('/api/analyze', {
      method: 'POST',
      body: JSON.stringify({ symbol, exchange: 'NSE', trade_type: 'SWING' }),
    }),
  scan: (symbols: string[]) =>
    req<{ count: number; valid_count: number; recommendations: TradeRecommendation[] }>(
      '/api/scan',
      { method: 'POST', body: JSON.stringify({ symbols, exchange: 'NSE', trade_type: 'SWING' }) },
    ),
  execute: (symbol: string) =>
    req<{ status: string; message: string; recommendation: TradeRecommendation }>(
      '/api/execute',
      { method: 'POST', body: JSON.stringify({ symbol, exchange: 'NSE', trade_type: 'SWING' }) },
    ),
  mark: () => req<{ positions: Position[]; portfolio: PortfolioSnapshot }>('/api/positions/mark', { method: 'POST' }),
  close: (position_id: number, reason = 'Manual close') =>
    req<Position>('/api/positions/close', {
      method: 'POST',
      body: JSON.stringify({ position_id, reason }),
    }),
  killSwitch: (active: boolean) =>
    req<EmergencyState>('/api/emergency/kill-switch', {
      method: 'POST',
      body: JSON.stringify({ active, reason: 'Dashboard kill switch' }),
    }),
  panicExit: () => req<{ closed: Position[]; emergency: EmergencyState }>('/api/emergency/panic-exit', { method: 'POST' }),
  report: (type: string) => req<{ title: string; summary: string; sections: Record<string, unknown> }>(`/api/reports/${type}`),
  performance: () =>
    req<{
      trades: number
      wins: number
      losses: number
      win_rate: number
      total_realized_pnl: number
      avg_win: number
      avg_loss: number
      expectancy: number
      total_fees: number
      capital: number
      drawdown_pct: number
      open_positions: number
    }>('/api/performance'),
  paperStatus: () =>
    req<{
      session: {
        running: boolean
        auto_execute: boolean
        cycles: number
        executed: number
        rejected: number
        valid_signals: number
        closed_by_mtm: number
        last_message: string
        interval_sec: number
        symbols: string[]
      }
      portfolio: PortfolioSnapshot
      risk: RiskStatus
    }>('/api/paper/status'),
  paperStart: (symbols: string[], interval_sec = 60, auto_execute = true) =>
    req<Record<string, unknown>>('/api/paper/start', {
      method: 'POST',
      body: JSON.stringify({ symbols, interval_sec, auto_execute, trade_type: 'SWING' }),
    }),
  paperStop: () => req<Record<string, unknown>>('/api/paper/stop', { method: 'POST' }),
  paperCycle: () => req<Record<string, unknown>>('/api/paper/cycle', { method: 'POST' }),
  paperReset: (confirm: boolean) =>
    req<Record<string, unknown>>('/api/paper/reset', {
      method: 'POST',
      body: JSON.stringify({ confirm }),
    }),
  strategies: () => req<Array<{ name: string; trade_type: string; class: string }>>('/api/strategies'),
  backtest: (symbol: string, strategy = 'swing_trend', period = '1y') =>
    req<{
      strategy: string
      symbol: string
      starting_capital: number
      ending_capital: number
      total_pnl: number
      total_return_pct: number
      max_drawdown_pct: number
      trades: number
      win_rate: number
      expectancy: number
      total_fees: number
      notes: string[]
    }>('/api/backtest', {
      method: 'POST',
      body: JSON.stringify({ symbol, strategy, period, exchange: 'NSE' }),
    }),
  brokerStatus: () =>
    req<{
      name: string
      connected: boolean
      mode: string
      message: string
      agent_mode: string
      live_ready: boolean
      note: string
    }>('/api/broker/status'),
}

export const inr = (n: number) =>
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(n)

export const inrDec = (n: number) =>
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 }).format(n)

export const pct = (n: number) => `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`

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
  trade_type?: string
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
  exit_price?: number | null
  exit_reason?: string | null
  closed_at?: string | null
  opened_at?: string
  capital_at_risk?: number
}

export type MarginBook = {
  capital: number
  initial_capital: number
  total_margin_used: number
  available_margin: number
  margin_utilization_pct: number
  fo_total_margin: number
  fo_total_exposure: number
  stocks_count: number
  fno_count: number
  unrealized_pnl: number
  by_segment: {
    stocks: { label: string; margin_used: number; exposure: number; open_positions: number }
    futures: { label: string; margin_used: number; exposure: number; open_positions: number }
    options: { label: string; margin_used: number; exposure: number; open_positions: number }
    etf: { label: string; margin_used: number; exposure: number; open_positions: number }
  }
  stocks: Array<{
    position_id: number | null
    symbol: string
    product: string
    segment: string
    trade_type: string
    side: string
    quantity: number
    entry_price: number
    ltp: number
    multiplier: number
    notional: number
    margin_required: number
    margin_pct: number
    exposure: number
    unrealized_pnl: number
    capital_at_risk: number
  }>
  fno: Array<{
    position_id: number | null
    symbol: string
    product: string
    segment: string
    trade_type: string
    side: string
    quantity: number
    entry_price: number
    ltp: number
    multiplier: number
    notional: number
    margin_required: number
    margin_pct: number
    exposure: number
    unrealized_pnl: number
    capital_at_risk: number
    option_type?: string | null
    strike?: number | null
    expiry?: string | null
    expiry_label?: string | null
    underlying_entry?: number | null
  }>
  notes: string[]
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
  margin: () => req<MarginBook>('/api/margin'),
  risk: () => req<RiskStatus>('/api/risk'),
  session: () => req<MarketSession>('/api/session'),
  positions: () => req<Position[]>('/api/positions'),
  journal: () => req<JournalEntry[]>('/api/journal'),
  watchlist: () => req<Quote[]>('/api/watchlist'),
  macro: () => req<Record<string, number>>('/api/macro'),
  emergency: () => req<EmergencyState>('/api/emergency'),
  analyze: (symbol: string, trade_type = 'SWING') =>
    req<TradeRecommendation>('/api/analyze', {
      method: 'POST',
      body: JSON.stringify({ symbol, exchange: 'NSE', trade_type }),
    }),
  scan: (symbols: string[], opts: { trade_type?: string; enable_fno?: boolean } = {}) =>
    req<{ count: number; valid_count: number; recommendations: TradeRecommendation[]; enable_fno?: boolean }>(
      '/api/scan',
      {
        method: 'POST',
        body: JSON.stringify({
          symbols,
          exchange: 'NSE',
          trade_type: opts.trade_type || 'SWING',
          enable_fno: opts.enable_fno ?? false,
        }),
      },
    ),
  execute: (symbol: string, trade_type = 'SWING') =>
    req<{ status: string; message: string; recommendation: TradeRecommendation }>(
      '/api/execute',
      { method: 'POST', body: JSON.stringify({ symbol, exchange: 'NSE', trade_type }) },
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
  paperStart: (
    symbols: string[],
    interval_sec = 60,
    auto_execute = true,
    opts: { enable_fno?: boolean; trade_type?: string; trade_types?: string[] } = {},
  ) =>
    req<Record<string, unknown>>('/api/paper/start', {
      method: 'POST',
      body: JSON.stringify({
        symbols,
        interval_sec,
        auto_execute,
        trade_type: opts.trade_type || 'SWING',
        enable_fno: opts.enable_fno ?? true,
        trade_types: opts.trade_types,
      }),
    }),
  paperStop: () => req<Record<string, unknown>>('/api/paper/stop', { method: 'POST' }),
  paperCycle: () => req<Record<string, unknown>>('/api/paper/cycle', { method: 'POST' }),
  paperReset: (confirm: boolean) =>
    req<Record<string, unknown>>('/api/paper/reset', {
      method: 'POST',
      body: JSON.stringify({ confirm }),
    }),
  orders: () =>
    req<
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
    >('/api/orders'),
  pnl: () =>
    req<{
      summary: {
        capital: number
        initial_capital: number
        total_pnl: number
        realized_pnl_today: number
        realized_pnl_week: number
        unrealized_pnl: number
        closed_realized_pnl: number
        total_fees: number
        drawdown_pct: number
        open_positions: number
        closed_trades: number
        wins: number
        losses: number
        win_rate: number
        mode: string
      }
      open_positions: Position[]
      closed_positions: Position[]
      journal: JournalEntry[]
      orders: Array<{
        id: number
        symbol: string
        side: string
        quantity: number
        price: number
        status: string
        message: string
        created_at: string
        position_status?: string | null
        entry_price?: number | null
        exit_price?: number | null
        unrealized_pnl?: number | null
        realized_pnl?: number | null
        pnl?: number | null
        pnl_pct?: number | null
        stop_loss?: number | null
        target_1?: number | null
        target_2?: number | null
        exit_reason?: string | null
      }>
    }>('/api/pnl'),
  chat: (message: string, history: Array<{ role: string; content: string }> = [], intent = 'chat') =>
    req<{
      role: string
      content: string
      timestamp: string
      sources: string[]
      mode?: string
      provider?: string
      model?: string | null
      error?: string | null
      analysis_stats?: Record<string, number> | null
    }>('/api/chat', {
      method: 'POST',
      body: JSON.stringify({ message, history, intent }),
    }),
  reviewLosses: (history: Array<{ role: string; content: string }> = []) =>
    req<{
      role: string
      content: string
      timestamp: string
      mode?: string
      provider?: string
      model?: string | null
      analysis_stats?: Record<string, number> | null
    }>('/api/chat/review-losses', {
      method: 'POST',
      body: JSON.stringify({ message: 'review losses', history, intent: 'loss_review' }),
    }),
  llmConfig: () =>
    req<{
      enabled: boolean
      provider: string
      model: string
      has_api_key: boolean
      key_source: string
      mode: string
      supported_providers: string[]
      hint: string
    }>('/api/chat/llm-config'),
  setLlmConfig: (api_key: string, provider: string, model = '') =>
    req<{
      enabled: boolean
      provider: string
      model: string
      has_api_key: boolean
      mode: string
      hint?: string
      key_source?: string
    }>('/api/chat/llm-config', {
      method: 'POST',
      body: JSON.stringify({ api_key, provider, model }),
    }),
  cycles: () =>
    req<
      Array<{
        id: number
        cycle_no: number
        message: string
        mtm_closed: number
        scanned: number
        valid_count: number
        executed_count: number
        rejected_count: number
        executed: Array<{ symbol: string; side?: string; quantity?: number; fill_price?: number; reason?: string; status?: string; message?: string }>
        rejected: Array<{ symbol: string; reason: string }>
        created_at: string
      }>
    >('/api/paper/cycles'),
  strategies: () =>
    req<{
      count: number
      strategies: Array<{
        name: string
        trade_type: string
        class: string
        family?: string
        phase?: number
        description?: string
      }>
      learning?: Record<string, unknown>
      note?: string
    }>('/api/strategies'),
  regime: () =>
    req<{
      regime: {
        regime: string
        confidence: number
        summary: string
        preferred_families: string[]
        adx: number
        atr_pct: number
        vix?: number | null
      }
      macro: { summary: string; avoid_new_risk: boolean; india_vix?: number }
    }>('/api/regime'),
  learning: () =>
    req<{
      updates: number
      leaderboard: Array<{
        strategy: string
        trades: number
        wins: number
        losses: number
        win_rate: number
        pnl: number
        weight: number
      }>
      weights: Record<string, number>
      mode: string
      note: string
    }>('/api/learning'),
  paperAdjustCapital: (opts: { delta?: number; set_to?: number; reason?: string }) =>
    req<{
      ok: boolean
      before: number
      after: number
      delta: number
      reason: string
      portfolio: PortfolioSnapshot
    }>('/api/paper/capital', {
      method: 'POST',
      body: JSON.stringify(opts),
    }),
  search: (q: string, limit = 20) =>
    req<{
      query: string
      universe_size: number
      results: Array<{
        symbol: string
        exchange: string
        price: number | null
        change_pct: number | null
        is_index: boolean
        fno: boolean
        segment: string
      }>
    }>(`/api/search?q=${encodeURIComponent(q)}&limit=${limit}`),
  fnoOverview: (symbol: string) =>
    req<{
      symbol: string
      exchange: string
      spot: number
      change_pct: number
      futures: { ltp: number; basis: number; basis_pct: number; lot_size: number }
      expiries: Array<{ expiry: string; label: string; kind: string; days_to_expiry: number }>
      default_expiry: string | null
      is_index: boolean
      atr: number
    }>(`/api/fno/${encodeURIComponent(symbol)}`),
  fnoChain: (symbol: string, expiry?: string) =>
    req<{
      symbol: string
      spot: number
      atm_strike: number
      strike_step: number
      expiry: string
      expiry_label: string
      days_to_expiry: number
      lot_size: number
      pcr: number | null
      max_pain: number
      iv_atm_pct: number
      note: string
      rows: Array<{
        strike: number
        is_atm: boolean
        call: { type: string; ltp: number; iv: number; oi: number; volume: number; change: number }
        put: { type: string; ltp: number; iv: number; oi: number; volume: number; change: number }
      }>
    }>(`/api/fno/${encodeURIComponent(symbol)}/chain${expiry ? `?expiry=${encodeURIComponent(expiry)}` : ''}`),
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

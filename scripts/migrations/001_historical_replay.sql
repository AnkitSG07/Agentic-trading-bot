-- Historical replay schema additions
CREATE TABLE IF NOT EXISTS historical_candles (
  id SERIAL PRIMARY KEY,
  symbol VARCHAR(50) NOT NULL,
  exchange VARCHAR(10) NOT NULL,
  timeframe VARCHAR(20) NOT NULL,
  timestamp TIMESTAMPTZ NOT NULL,
  open NUMERIC(12,4) NOT NULL,
  high NUMERIC(12,4) NOT NULL,
  low NUMERIC(12,4) NOT NULL,
  close NUMERIC(12,4) NOT NULL,
  volume INTEGER DEFAULT 0,
  CONSTRAINT uq_historical_candles UNIQUE(symbol, exchange, timeframe, timestamp)
);

CREATE TABLE IF NOT EXISTS replay_runs (
  id VARCHAR(40) PRIMARY KEY,
  status VARCHAR(20) NOT NULL,
  config JSONB NOT NULL,
  metrics JSONB,
  equity_curve JSONB,
  error TEXT,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS replay_trades (
  id SERIAL PRIMARY KEY,
  run_id VARCHAR(40) NOT NULL REFERENCES replay_runs(id),
  timestamp TIMESTAMPTZ NOT NULL,
  symbol VARCHAR(50) NOT NULL,
  exchange VARCHAR(10) NOT NULL,
  action VARCHAR(10) NOT NULL,
  quantity INTEGER NOT NULL,
  price NUMERIC(12,4) NOT NULL,
  fees NUMERIC(12,4),
  slippage_pct DOUBLE PRECISION,
  pnl NUMERIC(12,4),
  rationale TEXT
);

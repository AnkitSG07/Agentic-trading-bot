-- ============================================================
-- AgentTrader India - Database Initialization Script
-- Run automatically by Docker on first startup
-- ============================================================

-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Create tick_data as a TimescaleDB hypertable after table creation
-- (Tables are created by SQLAlchemy; this runs after)

-- Performance settings for trading workload
ALTER SYSTEM SET shared_buffers = '256MB';
ALTER SYSTEM SET effective_cache_size = '768MB';
ALTER SYSTEM SET work_mem = '16MB';
ALTER SYSTEM SET checkpoint_completion_target = '0.9';
ALTER SYSTEM SET wal_buffers = '16MB';

-- Grant permissions
GRANT ALL PRIVILEGES ON DATABASE trading_bot TO trader;

-- ─── VIEWS ──────────────────────────────────────────────────

-- Daily P&L view
CREATE OR REPLACE VIEW v_daily_pnl AS
SELECT
    DATE(opened_at AT TIME ZONE 'Asia/Kolkata') AS trade_date,
    COUNT(*) AS total_trades,
    COUNT(*) FILTER (WHERE net_pnl > 0) AS winning_trades,
    COUNT(*) FILTER (WHERE net_pnl < 0) AS losing_trades,
    ROUND(SUM(net_pnl)::numeric, 2) AS net_pnl,
    ROUND(SUM(realized_pnl)::numeric, 2) AS gross_pnl,
    ROUND(SUM(brokerage + stt)::numeric, 2) AS total_charges,
    ROUND(
        COUNT(*) FILTER (WHERE net_pnl > 0)::numeric /
        NULLIF(COUNT(*), 0) * 100, 1
    ) AS win_rate_pct
FROM positions
WHERE status = 'CLOSED'
GROUP BY DATE(opened_at AT TIME ZONE 'Asia/Kolkata')
ORDER BY trade_date DESC;

-- Strategy performance view
CREATE OR REPLACE VIEW v_strategy_performance AS
SELECT
    strategy,
    COUNT(*) AS total_trades,
    COUNT(*) FILTER (WHERE net_pnl > 0) AS winners,
    ROUND(SUM(net_pnl)::numeric, 2) AS total_pnl,
    ROUND(AVG(net_pnl)::numeric, 2) AS avg_pnl,
    ROUND(AVG(net_pnl) FILTER (WHERE net_pnl > 0)::numeric, 2) AS avg_win,
    ROUND(AVG(net_pnl) FILTER (WHERE net_pnl < 0)::numeric, 2) AS avg_loss,
    ROUND(
        COUNT(*) FILTER (WHERE net_pnl > 0)::numeric /
        NULLIF(COUNT(*), 0) * 100, 1
    ) AS win_rate_pct
FROM positions
WHERE status = 'CLOSED' AND strategy IS NOT NULL
GROUP BY strategy
ORDER BY total_pnl DESC;

-- Open positions with live context
CREATE OR REPLACE VIEW v_open_positions AS
SELECT
    p.id,
    p.broker,
    p.symbol,
    p.exchange,
    p.side,
    p.quantity,
    p.entry_price,
    p.stop_loss,
    p.target,
    p.strategy,
    p.opened_at,
    sl.broker_order_id AS sl_order_id,
    sl.sl_price AS current_sl,
    sl.sl_type
FROM positions p
LEFT JOIN sl_orders sl ON sl.position_id = p.id AND sl.is_active = TRUE
WHERE p.status = 'OPEN'
ORDER BY p.opened_at DESC;

-- Agent decision effectiveness
CREATE OR REPLACE VIEW v_agent_effectiveness AS
SELECT
    DATE(timestamp AT TIME ZONE 'Asia/Kolkata') AS decision_date,
    COUNT(*) AS total_decisions,
    SUM(signals_generated) AS total_signals,
    SUM(signals_executed) AS executed,
    SUM(signals_rejected) AS rejected,
    ROUND(
        SUM(signals_executed)::numeric /
        NULLIF(SUM(signals_generated), 0) * 100, 1
    ) AS execution_rate_pct,
    MODE() WITHIN GROUP (ORDER BY market_regime) AS dominant_regime
FROM agent_decisions
GROUP BY DATE(timestamp AT TIME ZONE 'Asia/Kolkata')
ORDER BY decision_date DESC;

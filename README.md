# 🤖 AgentTrader India

**Production-grade Agentic Trading Bot for Indian Markets**  
Zerodha (Kite API) + Dhan · Multi-Strategy · Powered by Claude AI

---

## Architecture

```
┌────────────────────────────────────────────────────────────┐
│                     AGENTTRADER INDIA                       │
├─────────────────┬──────────────────┬───────────────────────┤
│   DATA LAYER    │   AI AGENT BRAIN │   EXECUTION LAYER     │
│                 │                  │                        │
│ • WebSocket     │ • Claude API     │ • Order Manager        │
│   Tick Feed     │ • Strategy       │ • Risk Guard           │
│ • OHLCV Cache  │   Selection      │ • Position Monitor     │
│ • Indicators   │ • Signal Gen     │ • Zerodha Adapter      │
│ • Options OI   │ • Market Regime  │ • Dhan Adapter         │
│ • News Feed    │ • Review Loop    │ • Kill Switch          │
└─────────────────┴──────────────────┴───────────────────────┘
                           ↕
┌────────────────────────────────────────────────────────────┐
│              MONITORING DASHBOARD (React)                   │
│  Live P&L · Positions · AI Signals · Risk Gauges           │
└────────────────────────────────────────────────────────────┘
```

## Features

### 🧠 AI Agent (Claude-Powered)
- Multi-strategy decision engine (Momentum, Mean Reversion, Options Selling, Breakout)
- Dynamic market regime detection (trending/ranging/volatile)
- Configurable decision interval (default: 60s)
- Periodic strategy review and self-optimization
- Confidence scoring per signal (only trades above threshold)

### 📡 Broker Support
| Feature | Zerodha | Dhan |
|---|---|---|
| Auto-login (TOTP) | ✅ | ✅ (token-based) |
| Real-time WebSocket | ✅ KiteTicker | ✅ MarketFeed |
| Equity Trading | ✅ | ✅ |
| F&O Trading | ✅ | ✅ |
| Options Chain | ✅ | ✅ |
| Historical Data | ✅ | ✅ |

### 🛡️ Risk Management
- **Kill Switch**: Auto-stops trading on daily loss or drawdown breach
- **Position Sizing**: Kelly Criterion / Fixed / Volatility-adjusted
- **Stop Loss**: Auto-SL placement on every trade
- **Trailing Stop**: Dynamic SL adjustment as price moves in favor
- **Max Positions**: Configurable cap on open positions

### 📊 Strategies
- **Momentum**: EMA + RSI + MACD + Volume confirmation
- **Mean Reversion**: Bollinger Bands + RSI extremes
- **Options Selling**: Short premium, Iron Condor, Bull Put Spread
- **Breakout**: ATR-based with volume filter

---

## Quick Start

### 1. Prerequisites

```bash
# Python 3.11+
python --version

# Node.js 18+ (for dashboard)
node --version

# Docker + Docker Compose (recommended)
docker --version
```

### 2. Clone & Setup

```bash
git clone <your-repo>
cd trading-bot

# Copy env template
cp .env.example .env

# Edit credentials
nano .env
```

### 3. Configure `.env`

```env
# Zerodha
ZERODHA_API_KEY=xxx
ZERODHA_API_SECRET=xxx
ZERODHA_USER_ID=xxx
ZERODHA_TOTP_SECRET=xxx   # Base32 secret from TOTP app

# Dhan
DHAN_CLIENT_ID=xxx
DHAN_ACCESS_TOKEN=xxx     # Generate from Dhan web portal

# Anthropic (AI Brain)
ANTHROPIC_API_KEY=sk-ant-xxx

# Database
POSTGRES_PASSWORD=your_secure_password
```

### 4. Run with Docker (Recommended)

```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f trading-bot

# Dashboard: http://localhost:3000
# API docs:  http://localhost:8000/docs
```

### 5. Run without Docker

```bash
# Install Python dependencies
pip install -r requirements.txt

# Start PostgreSQL and Redis separately, then:
python main.py --mode paper    # Paper trading (safe)
python main.py --mode production  # Live trading (real money!)
```

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/health` | System health check |
| GET | `/api/portfolio/positions` | Open positions |
| GET | `/api/portfolio/funds` | Available capital |
| GET | `/api/orders` | Today's orders |
| POST | `/api/orders/manual` | Place manual order |
| DELETE | `/api/orders/{id}` | Cancel order |
| GET | `/api/risk/summary` | Risk stats |
| POST | `/api/risk/kill-switch/reset` | Reset kill switch |
| GET | `/api/agent/decisions` | AI decision history |
| WS | `/ws` | Live dashboard feed |

---

## Configuration

Edit `config/config.yaml` to customize:

```yaml
agent:
  confidence_threshold: 0.65     # Min AI confidence to trade
  decision_interval_seconds: 60  # How often AI evaluates

risk:
  max_daily_loss_pct: 2.0        # Kill switch threshold
  max_capital_per_trade_pct: 5.0 # Max position size
  max_open_positions: 10

strategies:
  options_selling:
    enabled: true
    iv_rank_threshold: 50         # Only sell options when IV is high
```

---

## Project Structure

```
trading-bot/
├── main.py                    # Entry point
├── config/
│   └── config.yaml            # Main configuration
├── brokers/
│   ├── base.py               # Abstract broker interface
│   ├── zerodha/adapter.py    # Zerodha Kite implementation
│   └── dhan/adapter.py       # Dhan implementation
├── agents/
│   └── brain.py              # AI agent (Claude API)
├── core/
│   ├── engine.py             # Trading orchestrator
│   ├── server.py             # FastAPI + WebSocket
│   └── notifier.py           # Telegram alerts
├── data/
│   └── indicators.py         # Technical indicators (pandas-ta)
├── risk/
│   └── manager.py            # Risk management + kill switch
├── dashboard/
│   └── src/App.jsx           # React dashboard
└── docker-compose.yml
```

---

## ⚠️ Important Disclaimers

1. **Paper trade first** — always run `--mode paper` for weeks before going live
2. **This bot trades real money** — set conservative risk limits until you're confident
3. **Backtesting ≠ live performance** — markets change; monitor closely
4. **API key security** — never commit `.env` to Git; rotate keys regularly
5. **SEBI compliance** — ensure your trading strategy complies with SEBI regulations
6. **No guarantees** — algorithmic trading involves significant financial risk

---

## Roadmap

- [ ] NSE Options chain real-time streaming  
- [ ] News sentiment (Google News / TickerTape API)
- [ ] Backtesting with vectorbt
- [ ] Multi-timeframe signal confluence
- [ ] Discord notifications
- [ ] Mobile app (React Native)


## Historical AI Replay (Paper Sim)

Paper Sim now supports backend historical replay using the same AI decision + risk checks pipeline used by the engine.

### 1) Backfill NSE historical candles

```bash
python main.py --api-only --backfill-symbols RELIANCE,TCS,INFY --backfill-start 2024-01-01 --backfill-end 2024-12-31
```

Or via API:

```bash
curl -X POST http://localhost:8000/api/historical/backfill \
  -H 'Content-Type: application/json' \
  -d '{"symbols":["RELIANCE","TCS"],"exchange":"NSE","timeframe":"day","start_date":"2024-01-01T00:00:00","end_date":"2024-12-31T00:00:00"}'
```

### 2) Run replay from CLI (debug mode)

```bash
python main.py --api-only --run-replay --replay-symbols RELIANCE,TCS
```

### 3) Run replay from UI/API

- Dashboard tab: **Paper Sim**
- API start endpoint: `POST /api/replay/runs`
- Status endpoint: `GET /api/replay/runs/{run_id}`
- Results endpoint: `GET /api/replay/runs/{run_id}/results`
- History endpoint: `GET /api/replay/runs`

### Weekend test example config

```json
{
  "symbols": ["RELIANCE", "TCS", "INFY"],
  "exchange": "NSE",
  "timeframe": "day",
  "start_date": "2024-01-01T00:00:00",
  "end_date": "2024-12-31T00:00:00",
  "initial_capital": 100000,
  "fee_pct": 0.0003,
  "slippage_pct": 0.0005
}
```

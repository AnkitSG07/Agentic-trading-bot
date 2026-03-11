import { useState, useEffect, useRef, useCallback } from "react";
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis,
  CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from "recharts";
import {
  TrendingUp, TrendingDown, Activity, Shield, Zap,
  AlertTriangle, Power, RefreshCw, ArrowUpRight,
  ArrowDownRight, Target, DollarSign, ChevronUp, ChevronDown,
  Cpu, Radio, Database, BarChart2,
} from "lucide-react";

const isProduction = window.location.hostname !== "localhost";
const API_BASE = import.meta.env.VITE_API_BASE ?? 
  (isProduction 
    ? "https://agentic-trading-bot-188e.onrender.com" 
    : "http://localhost:8000");

const WS_URL = import.meta.env.VITE_WS_URL ?? 
  API_BASE.replace(/^http/, "ws") + "/ws";

console.log("🔧 API Configuration:", { API_BASE, WS_URL, isProduction });

function useAPI(endpoint, interval = null) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetch_ = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}${endpoint}`);
      if (!res.ok) {
        if (res.status === 503) {
          console.warn(`⚠️ Engine not started for ${endpoint}`);
          setData(null);
          setError(null);
          setLoading(false);
          return;
        }
        throw new Error(`HTTP ${res.status}`);
      }
      const json = await res.json();
      setData(json);
      setError(null);
    } catch (e) {
      console.error(`❌ API error ${endpoint}:`, e.message);
      setError(e.message);
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [endpoint]);

  useEffect(() => {
    fetch_();
    if (interval) {
      const t = setInterval(fetch_, interval);
      return () => clearInterval(t);
    }
  }, [fetch_, interval]);

  return { data, loading, error, refetch: fetch_ };
}

function useWebSocket() {
  const [liveData, setLiveData] = useState(null);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);
  const reconnectRef = useRef(null);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    console.log("🔌 Connecting to WebSocket:", WS_URL);
    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log("✅ WebSocket connected");
      setConnected(true);
      clearInterval(reconnectRef.current);
    };

    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        setLiveData(data);
      } catch (err) {
        console.error("WebSocket parse error:", err);
      }
    };

    ws.onclose = () => {
      console.log("❌ WebSocket disconnected, reconnecting...");
      setConnected(false);
      reconnectRef.current = setTimeout(connect, 3000);
    };

    ws.onerror = (err) => {
      console.error("WebSocket error:", err);
      ws.close();
    };
  }, []);

  useEffect(() => {
    connect();
    return () => {
      clearInterval(reconnectRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return { liveData, connected };
}

const StatCard = ({ label, value, sub, trend, color = "#22d3ee", icon: Icon, loading }) => (
  <div style={{
    background: "rgba(15,23,42,0.85)", border: "1px solid rgba(255,255,255,0.07)",
    borderRadius: 12, padding: "18px 22px", backdropFilter: "blur(12px)",
    boxShadow: `0 0 30px ${color}10`, transition: "box-shadow 0.3s",
  }}>
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
      <div style={{ flex: 1 }}>
        <div style={{ color: "#475569", fontSize: 10, letterSpacing: 2, textTransform: "uppercase", marginBottom: 6 }}>{label}</div>
        {loading ? (
          <div style={{ height: 28, background: "#1e293b", borderRadius: 4, width: "60%", animation: "pulse 1.5s infinite" }} />
        ) : (
          <div style={{ color: "#f1f5f9", fontSize: 24, fontWeight: 700, fontFamily: "monospace", letterSpacing: -1 }}>{value}</div>
        )}
        {sub && <div style={{ color: trend > 0 ? "#10b981" : trend < 0 ? "#ef4444" : "#64748b", fontSize: 11, marginTop: 4 }}>{sub}</div>}
      </div>
      {Icon && (
        <div style={{ background: `${color}18`, borderRadius: 10, padding: 10 }}>
          <Icon size={18} color={color} />
        </div>
      )}
    </div>
  </div>
);

const Badge = ({ text, type }) => {
  const palette = {
    BUY: "#10b981", SELL: "#ef4444", SHORT: "#f59e0b", COVER: "#06b6d4",
    COMPLETE: "#10b981", CANCELLED: "#64748b", REJECTED: "#ef4444",
    OPEN: "#0ea5e9", PENDING: "#f59e0b",
    zerodha: "#387ed1", dhan: "#00b386",
    momentum: "#a78bfa", mean_reversion: "#fb923c",
    options_selling: "#f43f5e", breakout: "#facc15", scalping: "#22d3ee",
    NO_ACTION: "#475569", HOLD: "#475569",
    trending_up: "#10b981", trending_down: "#ef4444",
    ranging: "#f59e0b", high_volatility: "#f43f5e",
  };
  const c = palette[text] || palette[type] || "#64748b";
  return (
    <span style={{
      background: `${c}20`, color: c, border: `1px solid ${c}35`,
      borderRadius: 5, padding: "2px 7px", fontSize: 10, fontWeight: 700,
      letterSpacing: 0.8, textTransform: "uppercase", whiteSpace: "nowrap",
    }}>{text?.replace(/_/g, " ")}</span>
  );
};

const Pulse = ({ active, color = "#10b981" }) => (
  <div style={{
    width: 8, height: 8, borderRadius: "50%", background: active ? color : "#475569",
    boxShadow: active ? `0 0 8px ${color}` : "none",
    animation: active ? "pulse-dot 2s infinite" : "none",
  }} />
);

const PnLValue = ({ value, size = 18 }) => (
  <span style={{
    color: value >= 0 ? "#10b981" : "#ef4444",
    fontFamily: "monospace", fontWeight: 700, fontSize: size,
  }}>
    {value >= 0 ? "+" : ""}₹{Math.abs(value).toLocaleString("en-IN", { maximumFractionDigits: 0 })}
  </span>
);

export default function TradingDashboard() {
  const { liveData, connected } = useWebSocket();
  const [activeTab, setActiveTab] = useState("positions");
  const [pnlHistory, setPnlHistory] = useState([]);
  const [lastUpdate, setLastUpdate] = useState(new Date());
  const [engineRunning, setEngineRunning] = useState(false);
  const [startingEngine, setStartingEngine] = useState(false);

  const { data: ordersData, refetch: refetchOrders } = useAPI("/api/orders", 10000);
  const { data: analyticsData } = useAPI("/api/analytics/performance?days=30", 60000);
  const { data: agentData, refetch: refetchAgent } = useAPI("/api/agent/in-memory-decisions", 5000);
  const { data: riskEvents } = useAPI("/api/risk/events?limit=20", 30000);
  const { data: dailyHistory } = useAPI("/api/analytics/daily-history?days=14", 300000);

  useEffect(() => {
    if (!liveData?.pnl) return;
    setLastUpdate(new Date());
    setEngineRunning(liveData.engine_running || false);
    setPnlHistory(prev => {
      const now = new Date().toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
      const next = [...prev, { time: now, pnl: Math.round(liveData.pnl.total || 0) }];
      return next.slice(-80);
    });
  }, [liveData]);

  const handleStartEngine = async () => {
    setStartingEngine(true);
    try {
      const res = await fetch(`${API_BASE}/api/engine/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "paper" }),
      });
      const d = await res.json();
      console.log("Engine start response:", d);
      if (d.status === "starting") {
        setEngineRunning(true);
        alert("✅ Engine starting in paper trading mode!");
      }
    } catch (e) {
      console.error("Start engine error:", e);
      alert("❌ Failed to start engine: " + e.message);
    } finally {
      setTimeout(() => setStartingEngine(false), 3000);
    }
  };

  const handleStopEngine = async () => {
    try {
      await fetch(`${API_BASE}/api/engine/stop`, { method: "POST" });
      alert("Engine stopping...");
    } catch (e) {
      console.error("Stop engine error:", e);
    }
  };

  const handleResetKillSwitch = async () => {
    const code = prompt("Enter admin override code:");
    if (!code) return;
    try {
      const res = await fetch(`${API_BASE}/api/risk/kill-switch/reset`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ override_code: code }),
      });
      const d = await res.json();
      alert(d.status === "reset" ? "Kill switch reset ✅" : "Invalid code ❌");
    } catch (e) {
      alert("Error: " + e.message);
    }
  };

  const pnl = liveData?.pnl || {};
  const risk = liveData?.risk || {};
  const funds = liveData?.funds || {};
  const indices = liveData?.indices || {};
  const positions = liveData?.positions || [];
  const ticks = liveData?.ticks || {};
  const agentDecisions = agentData?.decisions || [];
  const orders = ordersData?.orders || [];
  const killSwitch = risk.kill_switch;

  const pnlColor = (pnl.total || 0) >= 0 ? "#10b981" : "#ef4444";

  return (
    <div style={{ minHeight: "100vh", background: "#060b14", color: "#e2e8f0", fontFamily: "'Inter', system-ui, sans-serif" }}>

      <div style={{
        position: "fixed", top: -300, left: "30%", width: 700, height: 500,
        borderRadius: "50%", background: "radial-gradient(ellipse, #0ea5e912, transparent 70%)",
        pointerEvents: "none", zIndex: 0,
      }} />

      <header style={{
        borderBottom: "1px solid rgba(255,255,255,0.06)",
        background: "rgba(6,11,20,0.96)", backdropFilter: "blur(20px)",
        position: "sticky", top: 0, zIndex: 100,
      }}>
        <div style={{ maxWidth: 1700, margin: "0 auto", padding: "0 24px", height: 58, display: "flex", alignItems: "center", justifyContent: "space-between" }}>

          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ background: "linear-gradient(135deg,#0ea5e9,#6366f1)", borderRadius: 9, width: 32, height: 32, display: "flex", alignItems: "center", justifyContent: "center" }}>
              <Zap size={16} color="white" fill="white" />
            </div>
            <div>
              <div style={{ fontWeight: 800, fontSize: 14, letterSpacing: -0.5 }}>AgentTrader India</div>
              <div style={{ fontSize: 9, color: "#334155", letterSpacing: 2 }}>ZERODHA · DHAN · CLAUDE AI</div>
            </div>
          </div>

          <div style={{ display: "flex", gap: 28 }}>
            {[
              { label: "NIFTY 50", val: indices.nifty, prev: 22000 },
              { label: "BANK NIFTY", val: indices.banknifty, prev: 47000 },
              { label: "INDIA VIX", val: indices.vix, prev: 14, warn: (indices.vix || 0) > 18 },
              { label: "PCR", val: agentDecisions[0]?.pcr || null },
            ].map(item => {
              const chg = item.prev ? ((item.val || item.prev) - item.prev) / item.prev * 100 : 0;
              return (
                <div key={item.label} style={{ textAlign: "center" }}>
                  <div style={{ fontSize: 9, color: "#334155", letterSpacing: 1.5 }}>{item.label}</div>
                  <div style={{ fontSize: 13, fontWeight: 700, fontFamily: "monospace", color: item.warn ? "#f59e0b" : "#e2e8f0" }}>
                    {item.val ? item.val.toFixed(2) : "—"}
                  </div>
                  {item.prev && item.val && (
                    <div style={{ fontSize: 9, color: chg >= 0 ? "#10b981" : "#ef4444" }}>
                      {chg >= 0 ? "▲" : "▼"} {Math.abs(chg).toFixed(2)}%
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <Pulse active={connected} color="#10b981" />
              <span style={{ fontSize: 10, color: connected ? "#10b981" : "#64748b" }}>
                {connected ? "LIVE" : "OFFLINE"}
              </span>
            </div>
            <div style={{ fontSize: 10, color: "#1e293b" }}>{lastUpdate.toLocaleTimeString("en-IN")}</div>

            <button
              onClick={engineRunning ? handleStopEngine : handleStartEngine}
              disabled={startingEngine}
              style={{
                background: engineRunning ? "#10b98115" : "#0ea5e915",
                border: `1px solid ${engineRunning ? "#10b98140" : "#0ea5e940"}`,
                borderRadius: 8, padding: "5px 14px", cursor: "pointer",
                display: "flex", alignItems: "center", gap: 6,
                fontSize: 10, fontWeight: 800, letterSpacing: 1,
                color: engineRunning ? "#10b981" : "#0ea5e9",
              }}
            >
              <Power size={11} />
              {startingEngine ? "STARTING..." : engineRunning ? "RUNNING" : "START ENGINE"}
            </button>
          </div>
        </div>
      </header>

      <main style={{ maxWidth: 1700, margin: "0 auto", padding: "20px 24px", position: "relative", zIndex: 1 }}>

        {killSwitch && (
          <div style={{
            background: "#ef444412", border: "1px solid #ef4444",
            borderRadius: 10, padding: "12px 20px", marginBottom: 16,
            display: "flex", alignItems: "center", gap: 12,
          }}>
            <AlertTriangle size={16} color="#ef4444" />
            <span style={{ color: "#ef4444", fontWeight: 700 }}>KILL SWITCH ACTIVE — Trading halted.</span>
            <span style={{ color: "#94a3b8", fontSize: 12 }}>{risk.kill_switch_reason}</span>
            <button
              onClick={handleResetKillSwitch}
              style={{ marginLeft: "auto", background: "#ef444420", border: "1px solid #ef4444", borderRadius: 6, padding: "4px 12px", color: "#ef4444", cursor: "pointer", fontSize: 11 }}
            >
              Reset
            </button>
          </div>
        )}

        {!engineRunning && (
          <div style={{
            background: "#0ea5e912", border: "1px solid #0ea5e9",
            borderRadius: 10, padding: "12px 20px", marginBottom: 16,
            display: "flex", alignItems: "center", gap: 12,
          }}>
            <Power size={16} color="#0ea5e9" />
            <span style={{ color: "#0ea5e9", fontWeight: 700 }}>Engine not running</span>
            <span style={{ color: "#94a3b8", fontSize: 12 }}>Click "START ENGINE" to begin trading</span>
          </div>
        )}

        {agentDecisions[0] && (
          <div style={{
            background: "rgba(14,165,233,0.05)", border: "1px solid rgba(14,165,233,0.12)",
            borderRadius: 10, padding: "9px 18px", marginBottom: 18,
            display: "flex", alignItems: "center", gap: 12,
          }}>
            <Cpu size={13} color="#0ea5e9" />
            <span style={{ fontSize: 11, color: "#475569" }}>AI Regime:</span>
            <Badge text={agentDecisions[0].market_regime} />
            <span style={{ fontSize: 11, color: "#334155", marginLeft: 8 }}>
              {agentDecisions[0].signals_generated} signals generated · {agentDecisions[0].signals_executed} executed · {agentDecisions[0].signals_rejected} rejected
            </span>
            <span style={{ marginLeft: "auto", fontSize: 10, color: "#334155" }}>
              Last decision: {agentDecisions[0].timestamp ? new Date(agentDecisions[0].timestamp).toLocaleTimeString("en-IN") : "—"}
            </span>
          </div>
        )}

        <div style={{ display: "grid", gridTemplateColumns: "repeat(6,1fr)", gap: 14, marginBottom: 20 }}>
          <StatCard label="Today P&L" value={<PnLValue value={pnl.total || 0} size={22} />}
            sub={`${(pnl.pct || 0) >= 0 ? "+" : ""}${(pnl.pct || 0).toFixed(2)}%`}
            trend={pnl.total || 0} color={pnlColor} icon={pnl.total >= 0 ? TrendingUp : TrendingDown} />
          <StatCard label="Realized P&L" value={<PnLValue value={pnl.realized || 0} size={22} />}
            sub="Booked" trend={pnl.realized} color="#10b981" icon={Target} />
          <StatCard label="Available Cash" value={`₹${((funds.available || 0) / 1000).toFixed(1)}K`}
            sub={`₹${((funds.used_margin || 0) / 1000).toFixed(1)}K in margin`} color="#0ea5e9" icon={DollarSign} />
          <StatCard label="Positions" value={positions.length}
            sub={`${10 - positions.length} slots free`} color="#6366f1" icon={Activity} />
          <StatCard label="Win Rate" value={`${(risk.win_rate || 0).toFixed(1)}%`}
            sub={`${risk.trades_today || 0} trades today`} color="#f59e0b" icon={Shield} />
          <StatCard label="Drawdown" value={`${(risk.drawdown_pct || 0).toFixed(2)}%`}
            sub={(risk.drawdown_pct || 0) < 2 ? "Safe zone" : "⚠️ Near limit"}
            trend={-(risk.drawdown_pct || 0)} color={(risk.drawdown_pct || 0) < 2 ? "#10b981" : "#ef4444"} icon={AlertTriangle} />
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 16, marginBottom: 20 }}>

          <div style={{ background: "rgba(15,23,42,0.85)", border: "1px solid rgba(255,255,255,0.07)", borderRadius: 12, padding: "18px 22px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
              <div>
                <div style={{ fontWeight: 700, fontSize: 13 }}>Intraday P&L</div>
                <div style={{ fontSize: 10, color: "#334155" }}>Real-time · Updates every second</div>
              </div>
              <PnLValue value={pnl.total || 0} size={20} />
            </div>
            <ResponsiveContainer width="100%" height={170}>
              <AreaChart data={pnlHistory} margin={{ top: 5, right: 5, bottom: 0, left: 0 }}>
                <defs>
                  <linearGradient id="pnlG" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={pnlColor} stopOpacity={0.25} />
                    <stop offset="95%" stopColor={pnlColor} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#0f172a" />
                <XAxis dataKey="time" tick={{ fill: "#334155", fontSize: 9 }} tickLine={false} interval={15} />
                <YAxis tick={{ fill: "#334155", fontSize: 9 }} tickLine={false} tickFormatter={v => `₹${(v / 1000).toFixed(1)}K`} />
                <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", borderRadius: 8, fontSize: 11 }}
                  formatter={v => [`₹${v.toLocaleString("en-IN")}`, "P&L"]} />
                <ReferenceLine y={0} stroke="#1e293b" strokeDasharray="4 2" />
                <Area type="monotone" dataKey="pnl" stroke={pnlColor} fill="url(#pnlG)" strokeWidth={2} dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>

          <div style={{ background: "rgba(15,23,42,0.85)", border: "1px solid rgba(255,255,255,0.07)", borderRadius: 12, padding: "18px 22px" }}>
            <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 4 }}>Risk Monitor</div>
            <div style={{ fontSize: 10, color: "#334155", marginBottom: 16 }}>Live limits</div>
            {[
              { label: "Daily Loss", used: Math.abs(Math.min(0, risk.daily_pnl_pct || 0)), max: 2.0, color: "#ef4444" },
              { label: "Drawdown", used: risk.drawdown_pct || 0, max: 8.0, color: "#f59e0b" },
              { label: "Positions", used: positions.length, max: 10, color: "#6366f1" },
              { label: "Margin Used", used: ((funds.used_margin || 0) / (funds.total || 1)) * 100, max: 80, color: "#0ea5e9" },
            ].map(r => {
              const pct = Math.min((r.used / r.max) * 100, 100);
              const isHigh = pct > 75;
              return (
                <div key={r.label} style={{ marginBottom: 14 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 5 }}>
                    <span style={{ fontSize: 11, color: "#64748b" }}>{r.label}</span>
                    <span style={{ fontSize: 11, fontFamily: "monospace", color: isHigh ? "#ef4444" : "#94a3b8" }}>
                      {typeof r.used === "number" && r.label === "Positions" ? `${Math.round(r.used)}/${r.max}` : `${r.used.toFixed(1)}%`}
                    </span>
                  </div>
                  <div style={{ height: 5, background: "#0f172a", borderRadius: 3, overflow: "hidden" }}>
                    <div style={{ width: `${pct}%`, height: "100%", background: isHigh ? "#ef4444" : r.color, borderRadius: 3, transition: "width 0.5s" }} />
                  </div>
                </div>
              );
            })}

            <div style={{
              marginTop: 16, paddingTop: 14, borderTop: "1px solid #1e293b",
              display: "flex", alignItems: "center", justifyContent: "space-between",
            }}>
              <span style={{ fontSize: 11, color: "#475569" }}>Kill Switch</span>
              <span style={{ fontSize: 11, fontWeight: 700, color: killSwitch ? "#ef4444" : "#10b981" }}>
                {killSwitch ? "⚠️ TRIGGERED" : "✓ SAFE"}
              </span>
            </div>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: 8 }}>
              <span style={{ fontSize: 11, color: "#475569" }}>Trading</span>
              <span style={{ fontSize: 11, fontWeight: 700, color: risk.trading_allowed ? "#10b981" : "#ef4444" }}>
                {risk.trading_allowed ? "✓ ALLOWED" : "✗ HALTED"}
              </span>
            </div>
          </div>
        </div>

        <div style={{ background: "rgba(15,23,42,0.85)", border: "1px solid rgba(255,255,255,0.07)", borderRadius: 12, overflow: "hidden" }}>
          <div style={{ display: "flex", borderBottom: "1px solid rgba(255,255,255,0.05)", padding: "0 20px" }}>
            {[
              { id: "positions", label: "Positions", count: positions.length },
              { id: "orders", label: "Orders", count: orders.length },
              { id: "signals", label: "AI Signals", count: agentDecisions.length },
              { id: "ticks", label: "Live Ticks" },
              { id: "history", label: "History" },
            ].map(tab => (
              <button key={tab.id} onClick={() => setActiveTab(tab.id)} style={{
                background: "none", border: "none", cursor: "pointer",
                padding: "13px 18px", fontSize: 11, fontWeight: 700,
                color: activeTab === tab.id ? "#0ea5e9" : "#334155",
                borderBottom: `2px solid ${activeTab === tab.id ? "#0ea5e9" : "transparent"}`,
                textTransform: "uppercase", letterSpacing: 1, transition: "color 0.2s",
                display: "flex", alignItems: "center", gap: 6,
              }}>
                {tab.label}
                {tab.count !== undefined && tab.count > 0 && (
                  <span style={{ background: "#0ea5e920", color: "#0ea5e9", borderRadius: 4, padding: "1px 5px", fontSize: 9 }}>
                    {tab.count}
                  </span>
                )}
              </button>
            ))}
            <button onClick={() => { refetchOrders(); refetchAgent(); }} style={{
              marginLeft: "auto", background: "none", border: "none", cursor: "pointer", color: "#334155", padding: "0 12px",
            }}>
              <RefreshCw size={13} />
            </button>
          </div>

          <div style={{ padding: "0 20px 20px", minHeight: 300 }}>

            {activeTab === "positions" && (
              positions.length === 0 ? (
                <div style={{ textAlign: "center", padding: "60px 0", color: "#334155", fontSize: 13 }}>
                  {engineRunning ? "No open positions" : "Start the engine to see positions"}
                </div>
              ) : (
                <table style={{ width: "100%", borderCollapse: "collapse" }}>
                  <thead>
                    <tr>{["Symbol", "Side", "Qty", "Avg Price", "LTP", "P&L", "P&L %", "Broker"].map(h => (
                      <th key={h} style={{ padding: "12px 8px", textAlign: "left", fontSize: 9, color: "#334155", letterSpacing: 2, textTransform: "uppercase", borderBottom: "1px solid #0f172a" }}>{h}</th>
                    ))}</tr>
                  </thead>
                  <tbody>
                    {positions.map((p, i) => (
                      <tr key={i} style={{ borderBottom: "1px solid #0a0f1a" }}>
                        <td style={{ padding: "13px 8px", fontWeight: 700, fontSize: 13 }}>{p.symbol}</td>
                        <td style={{ padding: "13px 8px" }}><Badge text={p.side} /></td>
                        <td style={{ padding: "13px 8px", fontFamily: "monospace", fontSize: 12 }}>{p.qty}</td>
                        <td style={{ padding: "13px 8px", fontFamily: "monospace", fontSize: 12 }}>₹{(p.avg || 0).toLocaleString("en-IN")}</td>
                        <td style={{ padding: "13px 8px", fontFamily: "monospace", fontSize: 12, color: "#94a3b8" }}>₹{(p.ltp || 0).toLocaleString("en-IN")}</td>
                        <td style={{ padding: "13px 8px" }}><PnLValue value={p.pnl || 0} size={13} /></td>
                        <td style={{ padding: "13px 8px", fontSize: 12, color: (p.pnl || 0) >= 0 ? "#10b981" : "#ef4444" }}>
                          {(p.pnl || 0) >= 0 ? <ArrowUpRight size={12} style={{ display: "inline" }} /> : <ArrowDownRight size={12} style={{ display: "inline" }} />}
                          {Math.abs(((p.ltp - p.avg) / p.avg * 100) || 0).toFixed(2)}%
                        </td>
                        <td style={{ padding: "13px 8px" }}><Badge text={p.broker} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )
            )}

            {activeTab === "orders" && (
              orders.length === 0 ? (
                <div style={{ textAlign: "center", padding: "60px 0", color: "#334155", fontSize: 13 }}>
                  {engineRunning ? "No orders today" : "Start the engine to see orders"}
                </div>
              ) : (
                <table style={{ width: "100%", borderCollapse: "collapse" }}>
                  <thead>
                    <tr>{["Time", "Symbol", "Side", "Qty", "Price", "Avg", "Status", "Tag"].map(h => (
                      <th key={h} style={{ padding: "12px 8px", textAlign: "left", fontSize: 9, color: "#334155", letterSpacing: 2, textTransform: "uppercase", borderBottom: "1px solid #0f172a" }}>{h}</th>
                    ))}</tr>
                  </thead>
                  <tbody>
                    {orders.map((o, i) => (
                      <tr key={i} style={{ borderBottom: "1px solid #0a0f1a" }}>
                        <td style={{ padding: "12px 8px", fontFamily: "monospace", fontSize: 11, color: "#475569" }}>{new Date(o.placed_at).toLocaleTimeString("en-IN")}</td>
                        <td style={{ padding: "12px 8px", fontWeight: 700, fontSize: 12 }}>{o.symbol}</td>
                        <td style={{ padding: "12px 8px" }}><Badge text={o.side} /></td>
                        <td style={{ padding: "12px 8px", fontFamily: "monospace", fontSize: 12 }}>{o.quantity}</td>
                        <td style={{ padding: "12px 8px", fontFamily: "monospace", fontSize: 12 }}>{o.price ? `₹${o.price.toLocaleString("en-IN")}` : "MKT"}</td>
                        <td style={{ padding: "12px 8px", fontFamily: "monospace", fontSize: 12, color: "#64748b" }}>{o.average_price ? `₹${o.average_price.toLocaleString("en-IN")}` : "—"}</td>
                        <td style={{ padding: "12px 8px" }}><Badge text={o.status} /></td>
                        <td style={{ padding: "12px 8px", fontSize: 10, color: "#475569" }}>{o.tag || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )
            )}

            {activeTab === "signals" && (
              <div>
                {agentDecisions.length === 0 ? (
                  <div style={{ textAlign: "center", padding: "60px 0", color: "#334155", fontSize: 13 }}>
                    {engineRunning ? "Waiting for first AI decision..." : "Start the engine to see AI decisions"}
                  </div>
                ) : agentDecisions.slice().reverse().map((d, i) => (
                  <div key={i} style={{
                    background: "#0a0f1a", border: "1px solid #1e293b",
                    borderRadius: 10, padding: "14px 16px", marginTop: 12,
                  }}>
                    <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 10 }}>
                      <span style={{ fontFamily: "monospace", fontSize: 10, color: "#334155" }}>
                        {new Date(d.timestamp).toLocaleTimeString("en-IN")}
                      </span>
                      {d.market_regime && <Badge text={d.market_regime} />}
                      <span style={{ fontSize: 10, color: "#475569" }}>
                        NIFTY {d.nifty?.toFixed(2)} · VIX {d.vix?.toFixed(2)} · PCR {d.pcr?.toFixed(2)}
                      </span>
                      <div style={{ marginLeft: "auto", display: "flex", gap: 10, fontSize: 11 }}>
                        <span style={{ color: "#10b981" }}>✅ {d.signals_executed} exec</span>
                        <span style={{ color: "#ef4444" }}>❌ {d.signals_rejected} rej</span>
                        <span style={{ color: "#64748b" }}>📊 {d.signals_generated} total</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {activeTab === "ticks" && (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 10, paddingTop: 14 }}>
                {Object.entries(ticks).length === 0 ? (
                  <div style={{ gridColumn: "1/-1", textAlign: "center", padding: "60px 0", color: "#334155", fontSize: 13 }}>
                    {engineRunning ? "Waiting for tick data..." : "Start the engine to see live ticks"}
                  </div>
                ) : Object.entries(ticks).map(([symbol, ltp]) => (
                  <div key={symbol} style={{ background: "#0a0f1a", border: "1px solid #1e293b", borderRadius: 8, padding: "12px 14px" }}>
                    <div style={{ fontSize: 11, fontWeight: 700 }}>{symbol}</div>
                    <div style={{ fontFamily: "monospace", fontSize: 16, fontWeight: 700, color: "#e2e8f0", marginTop: 4 }}>
                      ₹{ltp ? Number(ltp).toLocaleString("en-IN") : "—"}
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 2 }}>
                      <Pulse active={true} color="#0ea5e9" />
                      <span style={{ fontSize: 9, color: "#334155" }}>LIVE</span>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {activeTab === "history" && (
              <div style={{ paddingTop: 14 }}>
                <div style={{ fontWeight: 600, fontSize: 12, color: "#64748b", marginBottom: 12 }}>
                  Last 14 Days Performance
                </div>
                {dailyHistory?.history && dailyHistory.history.length > 0 ? (
                  <ResponsiveContainer width="100%" height={200}>
                    <BarChart data={dailyHistory.history.slice().reverse()}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#0f172a" />
                      <XAxis dataKey="date" tick={{ fill: "#334155", fontSize: 9 }} tickLine={false} />
                      <YAxis tick={{ fill: "#334155", fontSize: 9 }} tickLine={false} tickFormatter={v => `₹${(v/1000).toFixed(1)}K`} />
                      <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", borderRadius: 8, fontSize: 11 }}
                        formatter={v => [`₹${v.toLocaleString("en-IN")}`, "Net P&L"]} />
                      <ReferenceLine y={0} stroke="#1e293b" />
                      <Bar dataKey="net_pnl" fill="#10b981" radius={[3, 3, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                ) : (
                  <div style={{ textAlign: "center", padding: "40px 0", color: "#334155", fontSize: 12 }}>
                    No historical data yet. Data builds up after first trading day.
                  </div>
                )}

                {analyticsData && (
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 12, marginTop: 20 }}>
                    {[
                      { label: "30D Total P&L", value: `₹${(analyticsData.total_pnl || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 })}` },
                      { label: "Total Trades", value: analyticsData.total_trades || 0 },
                      { label: "Win Rate", value: `${(analyticsData.win_rate || 0).toFixed(1)}%` },
                      { label: "Avg Win", value: `₹${(analyticsData.avg_win || 0).toFixed(0)}` },
                      { label: "Avg Loss", value: `₹${(analyticsData.avg_loss || 0).toFixed(0)}` },
                      { label: "Profit Factor", value: (analyticsData.profit_factor || 0).toFixed(2) },
                    ].map(s => (
                      <div key={s.label} style={{ background: "#0a0f1a", border: "1px solid #1e293b", borderRadius: 8, padding: "12px 14px", textAlign: "center" }}>
                        <div style={{ fontSize: 9, color: "#334155", letterSpacing: 1.5, textTransform: "uppercase", marginBottom: 6 }}>{s.label}</div>
                        <div style={{ fontFamily: "monospace", fontWeight: 700, fontSize: 16 }}>{s.value}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

          </div>
        </div>
      </main>

      <style>{`
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
        @keyframes pulse-dot { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:0.6;transform:scale(0.85)} }
        * { box-sizing:border-box; margin:0; padding:0 }
        button:hover { opacity:0.85 }
        ::-webkit-scrollbar { width:4px }
        ::-webkit-scrollbar-track { background:#0a0f1a }
        ::-webkit-scrollbar-thumb { background:#1e293b; border-radius:2px }
      `}</style>
    </div>
  );
}

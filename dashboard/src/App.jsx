import { useState, useEffect, useRef, useCallback } from "react";
import {
  LineChart, Line, AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine
} from "recharts";
import { TrendingUp, TrendingDown, Activity, Shield, Zap, AlertTriangle, 
         Power, RefreshCw, ArrowUpRight, ArrowDownRight, Target, DollarSign } from "lucide-react";

// ── Mock data (replace with live WebSocket) ─────────────────────────────────

const generatePnLHistory = () => {
  let pnl = 0;
  return Array.from({ length: 78 }, (_, i) => {
    pnl += (Math.random() - 0.45) * 2000;
    return {
      time: `${9 + Math.floor(i / 6)}:${String((i % 6) * 10).padStart(2, "0")}`,
      pnl: Math.round(pnl),
      nifty: 22000 + Math.round((Math.random() - 0.5) * 200 * (i / 10)),
    };
  });
};

const MOCK_POSITIONS = [
  { symbol: "RELIANCE", side: "BUY", qty: 10, avg: 2445.5, ltp: 2478.3, pnl: 328, pnl_pct: 1.34, strategy: "MOMENTUM", broker: "zerodha" },
  { symbol: "HDFCBANK", side: "SELL", qty: 25, avg: 1634.2, ltp: 1621.8, pnl: 310, pnl_pct: 0.76, strategy: "MEAN_REV", broker: "dhan" },
  { symbol: "BANKNIFTY 47500 PE", side: "SELL", qty: 50, avg: 182.5, ltp: 154.3, pnl: 1410, pnl_pct: 15.45, strategy: "OPT_SELL", broker: "zerodha" },
  { symbol: "INFY", side: "BUY", qty: 30, avg: 1821.0, ltp: 1798.4, pnl: -678, pnl_pct: -1.24, strategy: "BREAKOUT", broker: "dhan" },
];

const MOCK_ORDERS = [
  { time: "14:32:18", symbol: "RELIANCE", side: "BUY", qty: 10, price: 2445.5, status: "COMPLETE", strategy: "MOMENTUM" },
  { time: "13:15:44", symbol: "BANKNIFTY 47500 PE", side: "SELL", qty: 50, price: 182.5, status: "COMPLETE", strategy: "OPT_SELL" },
  { time: "11:42:09", symbol: "HDFCBANK", side: "SELL", qty: 25, price: 1634.2, status: "COMPLETE", strategy: "MEAN_REV" },
  { time: "10:28:31", symbol: "INFY", side: "BUY", qty: 30, price: 1821.0, status: "COMPLETE", strategy: "BREAKOUT" },
  { time: "09:47:22", symbol: "NIFTY 22100 CE", side: "SELL", qty: 75, price: 89.0, status: "CANCELLED", strategy: "OPT_SELL" },
];

const MOCK_SIGNALS = [
  { time: "14:58:12", symbol: "TATAMOTORS", action: "BUY", confidence: 0.82, strategy: "momentum", rationale: "RSI 34→bullish reversal, MACD crossover, volume 2.1x avg" },
  { time: "14:45:03", symbol: "NIFTY 22200 CE", action: "SELL", confidence: 0.76, strategy: "options_selling", rationale: "IV Rank 68, short gamma play, 5 DTE, defined risk" },
  { time: "14:30:00", symbol: "SBIN", action: "NO_ACTION", confidence: 0.45, strategy: "breakout", rationale: "Insufficient volume confirmation, waiting for breakout retest" },
];

// ── Components ───────────────────────────────────────────────────────────────

const StatCard = ({ label, value, sub, trend, color = "#22d3ee", icon: Icon }) => (
  <div style={{
    background: "rgba(15,23,42,0.8)", border: "1px solid rgba(255,255,255,0.07)",
    borderRadius: 12, padding: "20px 24px", backdropFilter: "blur(12px)",
    boxShadow: `0 0 40px ${color}11`,
  }}>
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
      <div>
        <div style={{ color: "#64748b", fontSize: 11, letterSpacing: 2, textTransform: "uppercase", marginBottom: 8 }}>{label}</div>
        <div style={{ color: "#f1f5f9", fontSize: 26, fontWeight: 700, fontFamily: "monospace", letterSpacing: -1 }}>{value}</div>
        {sub && <div style={{ color: trend > 0 ? "#10b981" : trend < 0 ? "#ef4444" : "#64748b", fontSize: 12, marginTop: 4 }}>{sub}</div>}
      </div>
      {Icon && (
        <div style={{ background: `${color}18`, borderRadius: 10, padding: 10 }}>
          <Icon size={20} color={color} />
        </div>
      )}
    </div>
  </div>
);

const Badge = ({ text, type }) => {
  const colors = {
    BUY: { bg: "#10b98120", text: "#10b981", border: "#10b98140" },
    SELL: { bg: "#ef444420", text: "#ef4444", border: "#ef444440" },
    SHORT: { bg: "#f5953220", text: "#f59532", border: "#f5953240" },
    COMPLETE: { bg: "#10b98115", text: "#10b981", border: "transparent" },
    CANCELLED: { bg: "#64748b15", text: "#64748b", border: "transparent" },
    REJECTED: { bg: "#ef444415", text: "#ef4444", border: "transparent" },
    zerodha: { bg: "#387ed120", text: "#387ed1", border: "transparent" },
    dhan: { bg: "#00b38620", text: "#00b386", border: "transparent" },
    MOMENTUM: { bg: "#a78bfa20", text: "#a78bfa", border: "transparent" },
    MEAN_REV: { bg: "#fb923c20", text: "#fb923c", border: "transparent" },
    OPT_SELL: { bg: "#f43f5e20", text: "#f43f5e", border: "transparent" },
    BREAKOUT: { bg: "#facc1520", text: "#facc15", border: "transparent" },
    NO_ACTION: { bg: "#64748b15", text: "#64748b", border: "transparent" },
  };
  const c = colors[text] || colors[type] || { bg: "#ffffff10", text: "#94a3b8", border: "transparent" };
  return (
    <span style={{
      background: c.bg, color: c.text, border: `1px solid ${c.border}`,
      borderRadius: 6, padding: "2px 8px", fontSize: 10, fontWeight: 700,
      letterSpacing: 1, textTransform: "uppercase",
    }}>{text}</span>
  );
};

const ConfidenceBar = ({ value }) => (
  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
    <div style={{ flex: 1, height: 4, background: "#1e293b", borderRadius: 2, overflow: "hidden" }}>
      <div style={{
        width: `${value * 100}%`, height: "100%",
        background: value >= 0.75 ? "#10b981" : value >= 0.60 ? "#f59e0b" : "#ef4444",
        borderRadius: 2,
      }} />
    </div>
    <span style={{ color: "#94a3b8", fontSize: 11, minWidth: 32 }}>{Math.round(value * 100)}%</span>
  </div>
);

// ── Main Dashboard ────────────────────────────────────────────────────────────

export default function TradingDashboard() {
  const [pnlHistory] = useState(generatePnLHistory);
  const [liveData, setLiveData] = useState({
    totalPnL: 1370, pnlPct: 0.42, realizedPnL: 1850, unrealizedPnL: -480,
    availableCash: 487320, usedMargin: 152680, totalBalance: 640000,
    openPositions: 4, todayTrades: 5, winRate: 75, drawdown: 0.28,
    nifty: 22189.5, banknifty: 47834.2, vix: 13.4, pcr: 0.92,
    engineRunning: true, killSwitch: false, regime: "trending_up",
  });
  const [activeTab, setActiveTab] = useState("positions");
  const [isConnected, setIsConnected] = useState(true);
  const [lastUpdate, setLastUpdate] = useState(new Date());
  const wsRef = useRef(null);

  // Simulate live updates
  useEffect(() => {
    const interval = setInterval(() => {
      setLiveData(prev => ({
        ...prev,
        totalPnL: prev.totalPnL + (Math.random() - 0.48) * 150,
        unrealizedPnL: prev.unrealizedPnL + (Math.random() - 0.5) * 80,
        nifty: prev.nifty + (Math.random() - 0.5) * 3,
        banknifty: prev.banknifty + (Math.random() - 0.5) * 12,
      }));
      setLastUpdate(new Date());
    }, 2000);
    return () => clearInterval(interval);
  }, []);

  const pnlColor = liveData.totalPnL >= 0 ? "#10b981" : "#ef4444";
  const tabs = ["positions", "orders", "signals", "risk"];

  return (
    <div style={{
      minHeight: "100vh", background: "#060b14",
      color: "#e2e8f0", fontFamily: "'Inter', 'SF Pro Display', system-ui, sans-serif",
    }}>
      {/* Ambient glow */}
      <div style={{
        position: "fixed", top: -200, left: "50%", transform: "translateX(-50%)",
        width: 800, height: 400, borderRadius: "50%",
        background: "radial-gradient(ellipse, #0ea5e920 0%, transparent 70%)",
        pointerEvents: "none",
      }} />

      {/* Header */}
      <header style={{
        borderBottom: "1px solid rgba(255,255,255,0.06)",
        background: "rgba(6,11,20,0.95)", backdropFilter: "blur(20px)",
        position: "sticky", top: 0, zIndex: 100,
      }}>
        <div style={{ maxWidth: 1600, margin: "0 auto", padding: "0 24px", height: 60, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          
          {/* Logo */}
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <div style={{
              background: "linear-gradient(135deg, #0ea5e9, #6366f1)",
              borderRadius: 10, width: 34, height: 34, display: "flex",
              alignItems: "center", justifyContent: "center",
            }}>
              <Zap size={18} color="white" fill="white" />
            </div>
            <div>
              <div style={{ fontWeight: 800, fontSize: 15, letterSpacing: -0.5 }}>AgentTrader</div>
              <div style={{ fontSize: 10, color: "#475569", letterSpacing: 1.5 }}>INDIA · AI POWERED</div>
            </div>
          </div>

          {/* Index ticker */}
          <div style={{ display: "flex", gap: 32, alignItems: "center" }}>
            {[
              { label: "NIFTY 50", value: liveData.nifty.toFixed(2), chg: "+0.38%" },
              { label: "BANK NIFTY", value: liveData.banknifty.toFixed(2), chg: "+0.21%" },
              { label: "INDIA VIX", value: liveData.vix.toFixed(2), chg: "-2.14%", warn: liveData.vix > 18 },
              { label: "PCR", value: liveData.pcr.toFixed(2), chg: "" },
            ].map(item => (
              <div key={item.label} style={{ textAlign: "center" }}>
                <div style={{ fontSize: 10, color: "#475569", letterSpacing: 1 }}>{item.label}</div>
                <div style={{ fontSize: 13, fontWeight: 600, fontFamily: "monospace", color: item.warn ? "#f59e0b" : "#f1f5f9" }}>
                  {item.value}
                </div>
                {item.chg && (
                  <div style={{ fontSize: 10, color: item.chg.startsWith("-") ? "#ef4444" : "#10b981" }}>{item.chg}</div>
                )}
              </div>
            ))}
          </div>

          {/* Status bar */}
          <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <div style={{
                width: 8, height: 8, borderRadius: "50%",
                background: isConnected ? "#10b981" : "#ef4444",
                boxShadow: isConnected ? "0 0 8px #10b981" : "0 0 8px #ef4444",
                animation: isConnected ? "pulse 2s infinite" : "none",
              }} />
              <span style={{ fontSize: 11, color: "#64748b" }}>
                {isConnected ? "LIVE" : "DISCONNECTED"}
              </span>
            </div>
            <div style={{ fontSize: 11, color: "#334155" }}>
              {lastUpdate.toLocaleTimeString("en-IN")} IST
            </div>
            <div style={{
              background: liveData.engineRunning ? "#10b98115" : "#ef444415",
              border: `1px solid ${liveData.engineRunning ? "#10b98140" : "#ef444440"}`,
              borderRadius: 8, padding: "4px 12px", display: "flex", alignItems: "center", gap: 6,
              cursor: "pointer", fontSize: 11, fontWeight: 700,
              color: liveData.engineRunning ? "#10b981" : "#ef4444",
            }}>
              <Power size={12} />
              {liveData.engineRunning ? "ENGINE ON" : "ENGINE OFF"}
            </div>
          </div>
        </div>
      </header>

      <main style={{ maxWidth: 1600, margin: "0 auto", padding: "24px" }}>

        {/* Kill Switch Banner */}
        {liveData.killSwitch && (
          <div style={{
            background: "#ef444415", border: "1px solid #ef4444",
            borderRadius: 10, padding: "12px 20px", marginBottom: 20,
            display: "flex", alignItems: "center", gap: 12, color: "#ef4444",
          }}>
            <AlertTriangle size={18} />
            <strong>KILL SWITCH ACTIVE</strong> — Daily loss limit exceeded. All trading stopped.
          </div>
        )}

        {/* Regime Banner */}
        <div style={{
          background: "rgba(14,165,233,0.06)", border: "1px solid rgba(14,165,233,0.15)",
          borderRadius: 10, padding: "10px 20px", marginBottom: 20,
          display: "flex", alignItems: "center", gap: 12,
        }}>
          <Activity size={15} color="#0ea5e9" />
          <span style={{ fontSize: 12, color: "#64748b" }}>AI Market Regime:</span>
          <span style={{ fontSize: 12, fontWeight: 700, color: "#0ea5e9", textTransform: "uppercase", letterSpacing: 1 }}>
            {liveData.regime.replace(/_/g, " ")}
          </span>
          <span style={{ fontSize: 11, color: "#334155", marginLeft: "auto" }}>
            Session: Mid Session · Next decision in 23s
          </span>
        </div>

        {/* Stat Cards */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 16, marginBottom: 24 }}>
          <StatCard
            label="Total P&L Today"
            value={`₹${Math.abs(liveData.totalPnL).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`}
            sub={`${liveData.totalPnL >= 0 ? "+" : "-"}${Math.abs(liveData.pnlPct).toFixed(2)}%`}
            trend={liveData.totalPnL}
            color={liveData.totalPnL >= 0 ? "#10b981" : "#ef4444"}
            icon={liveData.totalPnL >= 0 ? TrendingUp : TrendingDown}
          />
          <StatCard
            label="Realized P&L"
            value={`₹${liveData.realizedPnL.toLocaleString("en-IN")}`}
            sub="Booked today"
            trend={liveData.realizedPnL}
            color="#10b981"
            icon={Target}
          />
          <StatCard
            label="Available Cash"
            value={`₹${(liveData.availableCash / 1000).toFixed(1)}K`}
            sub={`₹${(liveData.usedMargin / 1000).toFixed(1)}K in margin`}
            color="#0ea5e9"
            icon={DollarSign}
          />
          <StatCard
            label="Open Positions"
            value={liveData.openPositions}
            sub={`${10 - liveData.openPositions} slots remaining`}
            color="#6366f1"
            icon={Activity}
          />
          <StatCard
            label="Win Rate Today"
            value={`${liveData.winRate}%`}
            sub={`${liveData.todayTrades} trades`}
            color="#f59e0b"
            icon={Shield}
          />
          <StatCard
            label="Max Drawdown"
            value={`${liveData.drawdown.toFixed(2)}%`}
            sub={liveData.drawdown < 2 ? "Safe zone" : "⚠️ Near limit"}
            trend={-liveData.drawdown}
            color={liveData.drawdown < 2 ? "#10b981" : "#ef4444"}
            icon={AlertTriangle}
          />
        </div>

        {/* Charts Row */}
        <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 16, marginBottom: 24 }}>

          {/* P&L Chart */}
          <div style={{
            background: "rgba(15,23,42,0.8)", border: "1px solid rgba(255,255,255,0.07)",
            borderRadius: 12, padding: "20px 24px",
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
              <div>
                <div style={{ fontSize: 13, fontWeight: 600 }}>Intraday P&L</div>
                <div style={{ fontSize: 11, color: "#475569" }}>9:15 AM – 3:30 PM IST</div>
              </div>
              <div style={{ fontSize: 22, fontWeight: 700, fontFamily: "monospace", color: pnlColor }}>
                {liveData.totalPnL >= 0 ? "+" : ""}₹{Math.round(liveData.totalPnL).toLocaleString("en-IN")}
              </div>
            </div>
            <ResponsiveContainer width="100%" height={180}>
              <AreaChart data={pnlHistory} margin={{ top: 5, right: 5, bottom: 0, left: 0 }}>
                <defs>
                  <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis dataKey="time" tick={{ fill: "#475569", fontSize: 9 }} tickLine={false} interval={11} />
                <YAxis tick={{ fill: "#475569", fontSize: 9 }} tickLine={false} tickFormatter={v => `₹${(v/1000).toFixed(1)}K`} />
                <Tooltip
                  contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", borderRadius: 8 }}
                  labelStyle={{ color: "#94a3b8", fontSize: 11 }}
                  formatter={v => [`₹${v.toLocaleString("en-IN")}`, "P&L"]}
                />
                <ReferenceLine y={0} stroke="#334155" strokeDasharray="3 3" />
                <Area type="monotone" dataKey="pnl" stroke="#10b981" fill="url(#pnlGrad)" strokeWidth={2} dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>

          {/* Strategy breakdown */}
          <div style={{
            background: "rgba(15,23,42,0.8)", border: "1px solid rgba(255,255,255,0.07)",
            borderRadius: 12, padding: "20px 24px",
          }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>Strategy Allocation</div>
            <div style={{ fontSize: 11, color: "#475569", marginBottom: 20 }}>AI-managed weights</div>
            {[
              { name: "Options Selling", pct: 35, color: "#f43f5e", pnl: "+₹1,410" },
              { name: "Momentum", pct: 28, color: "#a78bfa", pnl: "+₹328" },
              { name: "Mean Reversion", pct: 22, color: "#fb923c", pnl: "+₹310" },
              { name: "Breakout", pct: 15, color: "#facc15", pnl: "-₹678" },
            ].map(s => (
              <div key={s.name} style={{ marginBottom: 14 }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 5 }}>
                  <span style={{ fontSize: 12, color: "#94a3b8" }}>{s.name}</span>
                  <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
                    <span style={{ fontSize: 11, color: s.pnl.startsWith("-") ? "#ef4444" : "#10b981", fontFamily: "monospace" }}>{s.pnl}</span>
                    <span style={{ fontSize: 11, color: "#64748b" }}>{s.pct}%</span>
                  </div>
                </div>
                <div style={{ height: 4, background: "#1e293b", borderRadius: 2, overflow: "hidden" }}>
                  <div style={{ width: `${s.pct}%`, height: "100%", background: s.color, borderRadius: 2 }} />
                </div>
              </div>
            ))}

            {/* Broker split */}
            <div style={{ marginTop: 20, paddingTop: 16, borderTop: "1px solid #1e293b" }}>
              <div style={{ fontSize: 11, color: "#475569", marginBottom: 10 }}>Broker Allocation</div>
              <div style={{ display: "flex", gap: 16 }}>
                {[
                  { name: "Zerodha", pct: 60, color: "#387ed1" },
                  { name: "Dhan", pct: 40, color: "#00b386" },
                ].map(b => (
                  <div key={b.name} style={{ flex: 1, background: `${b.color}15`, border: `1px solid ${b.color}30`, borderRadius: 8, padding: "8px 12px", textAlign: "center" }}>
                    <div style={{ fontSize: 11, color: b.color, fontWeight: 700 }}>{b.name}</div>
                    <div style={{ fontSize: 18, fontWeight: 800, color: "#f1f5f9" }}>{b.pct}%</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* Tabs */}
        <div style={{
          background: "rgba(15,23,42,0.8)", border: "1px solid rgba(255,255,255,0.07)",
          borderRadius: 12, overflow: "hidden",
        }}>
          {/* Tab bar */}
          <div style={{ display: "flex", borderBottom: "1px solid rgba(255,255,255,0.06)", padding: "0 20px" }}>
            {tabs.map(tab => (
              <button key={tab} onClick={() => setActiveTab(tab)} style={{
                background: "none", border: "none", cursor: "pointer",
                padding: "14px 20px", fontSize: 12, fontWeight: 600,
                color: activeTab === tab ? "#0ea5e9" : "#475569",
                borderBottom: `2px solid ${activeTab === tab ? "#0ea5e9" : "transparent"}`,
                textTransform: "uppercase", letterSpacing: 1, transition: "all 0.2s",
              }}>
                {tab}
                {tab === "positions" && (
                  <span style={{ marginLeft: 8, background: "#0ea5e920", color: "#0ea5e9", borderRadius: 4, padding: "1px 6px", fontSize: 10 }}>
                    {liveData.openPositions}
                  </span>
                )}
              </button>
            ))}
          </div>

          {/* Tab content */}
          <div style={{ padding: "0 20px 20px" }}>

            {/* Positions */}
            {activeTab === "positions" && (
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid #1e293b" }}>
                    {["Symbol", "Side", "Qty", "Avg Price", "LTP", "P&L", "P&L %", "Strategy", "Broker"].map(h => (
                      <th key={h} style={{ padding: "12px 8px", textAlign: "left", fontSize: 10, color: "#475569", letterSpacing: 1.5, textTransform: "uppercase", fontWeight: 500 }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {MOCK_POSITIONS.map((p, i) => (
                    <tr key={i} style={{ borderBottom: "1px solid #0f172a" }}>
                      <td style={{ padding: "14px 8px", fontWeight: 700, fontSize: 13 }}>{p.symbol}</td>
                      <td style={{ padding: "14px 8px" }}><Badge text={p.side} /></td>
                      <td style={{ padding: "14px 8px", fontFamily: "monospace" }}>{p.qty}</td>
                      <td style={{ padding: "14px 8px", fontFamily: "monospace" }}>₹{p.avg.toLocaleString("en-IN")}</td>
                      <td style={{ padding: "14px 8px", fontFamily: "monospace", color: "#94a3b8" }}>₹{p.ltp.toLocaleString("en-IN")}</td>
                      <td style={{ padding: "14px 8px", fontFamily: "monospace", fontWeight: 700, color: p.pnl >= 0 ? "#10b981" : "#ef4444" }}>
                        {p.pnl >= 0 ? "+" : ""}₹{p.pnl.toLocaleString("en-IN")}
                      </td>
                      <td style={{ padding: "14px 8px", color: p.pnl_pct >= 0 ? "#10b981" : "#ef4444", fontSize: 12 }}>
                        {p.pnl_pct >= 0 ? <ArrowUpRight size={12} style={{display:"inline"}} /> : <ArrowDownRight size={12} style={{display:"inline"}} />}
                        {Math.abs(p.pnl_pct).toFixed(2)}%
                      </td>
                      <td style={{ padding: "14px 8px" }}><Badge text={p.strategy} /></td>
                      <td style={{ padding: "14px 8px" }}><Badge text={p.broker} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            {/* Orders */}
            {activeTab === "orders" && (
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid #1e293b" }}>
                    {["Time", "Symbol", "Side", "Qty", "Price", "Status", "Strategy"].map(h => (
                      <th key={h} style={{ padding: "12px 8px", textAlign: "left", fontSize: 10, color: "#475569", letterSpacing: 1.5, textTransform: "uppercase", fontWeight: 500 }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {MOCK_ORDERS.map((o, i) => (
                    <tr key={i} style={{ borderBottom: "1px solid #0f172a" }}>
                      <td style={{ padding: "14px 8px", fontFamily: "monospace", fontSize: 12, color: "#64748b" }}>{o.time}</td>
                      <td style={{ padding: "14px 8px", fontWeight: 700, fontSize: 13 }}>{o.symbol}</td>
                      <td style={{ padding: "14px 8px" }}><Badge text={o.side} /></td>
                      <td style={{ padding: "14px 8px", fontFamily: "monospace" }}>{o.qty}</td>
                      <td style={{ padding: "14px 8px", fontFamily: "monospace" }}>₹{o.price.toLocaleString("en-IN")}</td>
                      <td style={{ padding: "14px 8px" }}><Badge text={o.status} /></td>
                      <td style={{ padding: "14px 8px" }}><Badge text={o.strategy} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            {/* AI Signals */}
            {activeTab === "signals" && (
              <div>
                <div style={{ color: "#475569", fontSize: 11, padding: "12px 0 4px", letterSpacing: 1 }}>
                  LATEST AI AGENT DECISIONS
                </div>
                {MOCK_SIGNALS.map((s, i) => (
                  <div key={i} style={{
                    background: "#0f172a", border: "1px solid #1e293b",
                    borderRadius: 10, padding: "16px", marginBottom: 10,
                  }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
                      <span style={{ fontSize: 11, color: "#475569", fontFamily: "monospace" }}>{s.time}</span>
                      <span style={{ fontWeight: 800, fontSize: 14 }}>{s.symbol}</span>
                      <Badge text={s.action} />
                      <Badge text={s.strategy} />
                      <div style={{ marginLeft: "auto", minWidth: 140 }}>
                        <ConfidenceBar value={s.confidence} />
                      </div>
                    </div>
                    <div style={{ fontSize: 12, color: "#64748b", lineHeight: 1.6 }}>
                      🤖 {s.rationale}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Risk */}
            {activeTab === "risk" && (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 16, paddingTop: 16 }}>
                {[
                  { label: "Daily Loss Limit", used: 0.21, max: 2.0, current: "0.21%", unit: "% of capital", color: "#10b981" },
                  { label: "Max Drawdown", used: 0.28, max: 8.0, current: "0.28%", unit: "% of capital", color: "#0ea5e9" },
                  { label: "Position Limit", used: 4, max: 10, current: "4/10", unit: "positions", color: "#a78bfa" },
                  { label: "Capital per Trade", used: 3.2, max: 5.0, current: "3.2%", unit: "% max allowed", color: "#f59e0b" },
                  { label: "Win Rate (7D)", used: 62, max: 100, current: "62%", unit: "target > 55%", color: "#10b981" },
                  { label: "Risk/Reward Avg", used: 2.1, max: 3.0, current: "2.1:1", unit: "target > 1.5:1", color: "#22d3ee" },
                ].map(r => (
                  <div key={r.label} style={{
                    background: "#0f172a", border: "1px solid #1e293b",
                    borderRadius: 10, padding: "18px",
                  }}>
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12 }}>
                      <div style={{ fontSize: 12, color: "#64748b" }}>{r.label}</div>
                      <div style={{ fontSize: 16, fontWeight: 700, fontFamily: "monospace", color: r.color }}>{r.current}</div>
                    </div>
                    <div style={{ height: 6, background: "#1e293b", borderRadius: 3, overflow: "hidden", marginBottom: 6 }}>
                      <div style={{
                        width: `${Math.min((r.used / r.max) * 100, 100)}%`,
                        height: "100%", background: r.color, borderRadius: 3,
                        opacity: r.used / r.max > 0.8 ? 1 : 0.7,
                      }} />
                    </div>
                    <div style={{ fontSize: 10, color: "#334155" }}>{r.unit}</div>
                  </div>
                ))}
              </div>
            )}

          </div>
        </div>
      </main>

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.5; }
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-track { background: #0f172a; }
        ::-webkit-scrollbar-thumb { background: #1e293b; border-radius: 2px; }
      `}</style>
    </div>
  );
}

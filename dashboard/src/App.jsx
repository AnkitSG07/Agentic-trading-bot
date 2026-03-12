import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis,
  CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from "recharts";
import {
  TrendingUp, TrendingDown, Activity, Shield, Zap,
  AlertTriangle, Power, RefreshCw, ArrowUpRight,
  ArrowDownRight, Target, DollarSign, ChevronUp, ChevronDown,
  Cpu, Radio, Database, BarChart2, Bell, Settings,
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
    risk_passed: "#10b981", risk_rejected: "#ef4444", preview_layout: "#22d3ee",
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

const DEFAULT_MOCK_SIGNALS = [
  {
    symbol: "RELIANCE",
    action: "BUY",
    strategy: "breakout",
    confidence: 0.82,
    entry_price: 2450,
    stop_loss: 2420,
    target: 2510,
    rationale: "MACD bullish crossover with 1.8x volume and resistance breakout.",
    risk_status: "approved",
  },
  {
    symbol: "NIFTY",
    action: "NO_ACTION",
    strategy: "scalping",
    confidence: 0.41,
    entry_price: null,
    stop_loss: null,
    target: null,
    rationale: "Weak confluence and choppy range, waiting for clean setup.",
    risk_status: "rejected",
  },
  {
    symbol: "HDFCBANK",
    action: "SELL",
    strategy: "mean_reversion",
    confidence: 0.74,
    entry_price: 1612,
    stop_loss: 1628,
    target: 1578,
    rationale: "RSI reverted from overbought zone with lower high near resistance.",
    risk_status: "approved",
  },
];

export default function TradingDashboard() {
  const { liveData, connected } = useWebSocket();
  const [activeTab, setActiveTab] = useState("positions");
  const [pnlHistory, setPnlHistory] = useState([]);
  const [tickHistory, setTickHistory] = useState({});
  const [prevTicks, setPrevTicks] = useState({});
  const [eventTape, setEventTape] = useState([]);
  const [lastUpdate, setLastUpdate] = useState(new Date());
  const [engineRunning, setEngineRunning] = useState(false);
  const [startingEngine, setStartingEngine] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const [strategyFilter, setStrategyFilter] = useState("all");
  const [brokerFilter, setBrokerFilter] = useState("all");
  const [timeRange, setTimeRange] = useState("today");
  const [showAlerts, setShowAlerts] = useState(false);
  const [acknowledgedAlerts, setAcknowledgedAlerts] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem("acknowledged_alerts") || "[]");
    } catch {
      return [];
    }
  });
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [uiSettings, setUiSettings] = useState(() => {
    try {
      return {
        compactMode: false,
        soundAlerts: false,
        colorBlindMode: false,
        ...JSON.parse(localStorage.getItem("dashboard_ui_settings") || "{}"),
      };
    } catch {
      return { compactMode: false, soundAlerts: false, colorBlindMode: false };
    }
  });

  const refreshInterval = useMemo(() => {
    if (timeRange === "15m") return 4000;
    if (timeRange === "1h") return 7000;
    return 10000;
  }, [timeRange]);

  const { data: ordersData, refetch: refetchOrders } = useAPI("/api/orders", refreshInterval);
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

  useEffect(() => {
    const nextTicks = liveData?.ticks;
    if (!nextTicks || Object.keys(nextTicks).length === 0) return;

    setPrevTicks((prev) => ({ ...prev, ...nextTicks }));
    setTickHistory((prev) => {
      const next = { ...prev };
      Object.entries(nextTicks).forEach(([symbol, value]) => {
        const price = Number(value || 0);
        if (!price) return;
        const series = [...(next[symbol] || []), price];
        next[symbol] = series.slice(-30);
      });
      return next;
    });
  }, [liveData?.ticks]);

  useEffect(() => {
    const wsEvents = liveData?.agent_events || [];
    const apiEvents = agentData?.agent_events || [];
    const merged = [...apiEvents, ...wsEvents]
      .filter((e) => e?.timestamp && e?.message)
      .sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
    const dedup = [];
    const seen = new Set();
    merged.forEach((e) => {
      const key = `${e.timestamp}-${e.message}`;
      if (!seen.has(key)) {
        dedup.push(e);
        seen.add(key);
      }
    });
    setEventTape(dedup.slice(-20));
  }, [liveData?.agent_events, agentData?.agent_events]);

  useEffect(() => {
    localStorage.setItem("dashboard_ui_settings", JSON.stringify(uiSettings));
  }, [uiSettings]);

  useEffect(() => {
    localStorage.setItem("acknowledged_alerts", JSON.stringify(acknowledgedAlerts));
  }, [acknowledgedAlerts]);

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
  const agentStatus = liveData?.agent_status || agentData?.agent_status || {};
  const orders = ordersData?.orders || [];
  const killSwitch = risk.kill_switch;
  const latestDecision = agentDecisions.length ? agentDecisions[agentDecisions.length - 1] : null;
  const latestSignals = (latestDecision?.signals || latestDecision?.signals_raw || []).slice(0, 3);
  const reasoningSignals = latestSignals.length > 0 ? latestSignals : DEFAULT_MOCK_SIGNALS;
  const progressPct = Number(agentStatus?.progress_pct || 0);
  const selectedStrategy = agentStatus?.selected_strategy || latestSignals[0]?.strategy || null;
  const liveCycleId = agentStatus?.cycle_id || "preview";
  const isPreviewMode = latestSignals.length === 0;

  const pnlColor = (pnl.total || 0) >= 0 ? "#10b981" : "#ef4444";
  const rowPadding = uiSettings.compactMode ? "8px 8px" : "13px 8px";

  const timeRangeMs = useMemo(() => {
    if (timeRange === "15m") return 15 * 60 * 1000;
    if (timeRange === "1h") return 60 * 60 * 1000;
    return 24 * 60 * 60 * 1000;
  }, [timeRange]);

  const strategyOptions = useMemo(() => {
    const set = new Set(DEFAULT_MOCK_SIGNALS.map((s) => s.strategy));
    reasoningSignals.forEach((s) => s?.strategy && set.add(s.strategy));
    agentDecisions.forEach((d) => d?.selected_strategy && set.add(d.selected_strategy));
    return ["all", ...Array.from(set)];
  }, [reasoningSignals, agentDecisions]);

  const brokerOptions = useMemo(() => {
    const set = new Set(["all"]);
    positions.forEach((p) => p?.broker && set.add(p.broker));
    orders.forEach((o) => o?.broker && set.add(o.broker));
    return Array.from(set);
  }, [positions, orders]);

  const matchesSearch = useCallback((parts) => {
    if (!searchTerm) return true;
    const haystack = parts.filter(Boolean).join(" ").toLowerCase();
    return haystack.includes(searchTerm.toLowerCase());
  }, [searchTerm]);

  const nowTs = Date.now();

  const filteredPositions = useMemo(() => positions.filter((p) => {
    const strategyMatch = strategyFilter === "all" || (p.strategy || "").toLowerCase() === strategyFilter.toLowerCase();
    const brokerMatch = brokerFilter === "all" || (p.broker || "").toLowerCase() === brokerFilter.toLowerCase();
    return strategyMatch && brokerMatch && matchesSearch([p.symbol, p.side, p.broker, p.tag]);
  }), [positions, strategyFilter, brokerFilter, matchesSearch]);

  const filteredOrders = useMemo(() => orders.filter((o) => {
    const strategyMatch = strategyFilter === "all" || (o.strategy || "").toLowerCase() === strategyFilter.toLowerCase();
    const brokerMatch = brokerFilter === "all" || (o.broker || "").toLowerCase() === brokerFilter.toLowerCase();
    const ts = o.placed_at ? new Date(o.placed_at).getTime() : nowTs;
    const inTime = nowTs - ts <= timeRangeMs;
    return inTime && strategyMatch && brokerMatch && matchesSearch([o.symbol, o.side, o.status, o.tag, o.broker]);
  }), [orders, strategyFilter, brokerFilter, timeRangeMs, nowTs, matchesSearch]);

  const filteredDecisions = useMemo(() => agentDecisions.filter((d) => {
    const strategyMatch = strategyFilter === "all" || (d.selected_strategy || "").toLowerCase() === strategyFilter.toLowerCase();
    const ts = d.timestamp ? new Date(d.timestamp).getTime() : nowTs;
    const inTime = nowTs - ts <= timeRangeMs;
    return inTime && strategyMatch && matchesSearch([d.market_commentary, d.commentary, d.market_regime]);
  }), [agentDecisions, strategyFilter, timeRangeMs, nowTs, matchesSearch]);

  const filteredSignals = useMemo(() => reasoningSignals.filter((s) => {
    const strategyMatch = strategyFilter === "all" || (s.strategy || "").toLowerCase() === strategyFilter.toLowerCase();
    return strategyMatch && matchesSearch([s.symbol, s.action, s.strategy, s.rationale]);
  }), [reasoningSignals, strategyFilter, matchesSearch]);

  const filteredEventTape = useMemo(() => eventTape.filter((e) => {
    const ts = e.timestamp ? new Date(e.timestamp).getTime() : nowTs;
    const inTime = nowTs - ts <= timeRangeMs;
    return inTime && matchesSearch([e.message, e.level, e.source]);
  }), [eventTape, nowTs, timeRangeMs, matchesSearch]);

  const filteredTicksEntries = useMemo(() => Object.entries(ticks).filter(([symbol]) => matchesSearch([symbol])), [ticks, matchesSearch]);

  const exposureData = useMemo(() => {
    const rows = filteredPositions.map((p) => {
      const qty = Number(p.qty || 0);
      const ltp = Number(p.ltp || p.avg || 0);
      const notional = Math.abs(qty * ltp);
      const side = (p.side || "").toUpperCase();
      const sector = p.sector || p.symbol?.split("-")[0] || "Other";
      return { symbol: p.symbol, side, notional, sector };
    }).filter((r) => r.notional > 0);
    const total = rows.reduce((sum, r) => sum + r.notional, 0) || 1;
    const byPosition = rows
      .map((r) => ({ ...r, weightPct: (r.notional / total) * 100 }))
      .sort((a, b) => b.notional - a.notional)
      .slice(0, 5);
    const sectorMap = {};
    rows.forEach((r) => {
      sectorMap[r.sector] = (sectorMap[r.sector] || 0) + r.notional;
    });
    const bySector = Object.entries(sectorMap).map(([sector, value]) => ({ sector, value, weightPct: (value / total) * 100 }))
      .sort((a, b) => b.value - a.value);
    const longExposure = rows.filter((r) => r.side === "BUY").reduce((sum, r) => sum + r.notional, 0);
    const shortExposure = rows.filter((r) => r.side === "SELL" || r.side === "SHORT").reduce((sum, r) => sum + r.notional, 0);
    return {
      total,
      byPosition,
      bySector,
      longPct: (longExposure / total) * 100,
      shortPct: (shortExposure / total) * 100,
      concentrationFlag: (byPosition[0]?.weightPct || 0) > 35,
    };
  }, [filteredPositions]);

  const executionMetrics = useMemo(() => {
    const total = filteredOrders.length;
    const filled = filteredOrders.filter((o) => ["COMPLETE", "FILLED"].includes((o.status || "").toUpperCase()));
    const rejected = filteredOrders.filter((o) => (o.status || "").toUpperCase() === "REJECTED");
    const slippages = filled
      .map((o) => {
        const intended = Number(o.price || 0);
        const avg = Number(o.average_price || 0);
        return intended > 0 && avg > 0 ? ((avg - intended) / intended) * 100 : null;
      })
      .filter((v) => v !== null);
    const latencies = filled
      .map((o) => {
        const a = o.placed_at ? new Date(o.placed_at).getTime() : null;
        const b = o.filled_at ? new Date(o.filled_at).getTime() : null;
        return a && b ? (b - a) / 1000 : null;
      })
      .filter((v) => v !== null);
    const rejectionReasons = {};
    rejected.forEach((o) => {
      const reason = o.reject_reason || o.status_message || "unknown";
      rejectionReasons[reason] = (rejectionReasons[reason] || 0) + 1;
    });
    return {
      fillRate: total ? (filled.length / total) * 100 : 0,
      avgSlippage: slippages.length ? slippages.reduce((a, b) => a + b, 0) / slippages.length : null,
      avgLatency: latencies.length ? latencies.reduce((a, b) => a + b, 0) / latencies.length : null,
      rejectedCount: rejected.length,
      topRejections: Object.entries(rejectionReasons).sort((a, b) => b[1] - a[1]).slice(0, 3),
    };
  }, [filteredOrders]);

  const strategyLeaderboard = useMemo(() => {
    const map = {};
    filteredDecisions.forEach((d) => {
      const key = d.selected_strategy || "unknown";
      map[key] = map[key] || { strategy: key, signals: 0, executed: 0, rejected: 0, confidenceSum: 0, confidenceCount: 0, netPnl: 0 };
      map[key].signals += Number(d.signals_generated || 0);
      map[key].executed += Number(d.signals_executed || 0);
      map[key].rejected += Number(d.signals_rejected || 0);
      if (typeof d.avg_confidence === "number") {
        map[key].confidenceSum += d.avg_confidence;
        map[key].confidenceCount += 1;
      }
      map[key].netPnl += Number(d.net_pnl || 0);
    });
    return Object.values(map)
      .map((r) => ({
        ...r,
        executionRate: r.signals ? (r.executed / r.signals) * 100 : 0,
        avgConfidence: r.confidenceCount ? (r.confidenceSum / r.confidenceCount) * 100 : 0,
      }))
      .sort((a, b) => b.netPnl - a.netPnl);
  }, [filteredDecisions]);

  const alerts = useMemo(() => {
    const derived = [];
    if (killSwitch) {
      derived.push({ id: `kill-${risk.kill_switch_reason || "active"}`, severity: "critical", source: "risk", message: risk.kill_switch_reason || "Kill switch active", timestamp: new Date().toISOString() });
    }
    if (!risk.trading_allowed) {
      derived.push({ id: "trading-halted", severity: "critical", source: "risk", message: "Trading currently halted", timestamp: new Date().toISOString() });
    }
    if ((risk.drawdown_pct || 0) > 5) {
      derived.push({ id: `drawdown-${risk.drawdown_pct}`, severity: "warn", source: "risk", message: `Drawdown elevated at ${(risk.drawdown_pct || 0).toFixed(2)}%`, timestamp: new Date().toISOString() });
    }
    (riskEvents?.events || []).forEach((e, idx) => {
      derived.push({
        id: `${e.timestamp || idx}-${e.message || e.event}`,
        severity: e.level === "error" ? "critical" : e.level === "warning" ? "warn" : "info",
        source: "risk",
        message: e.message || e.event || "Risk event",
        timestamp: e.timestamp || new Date().toISOString(),
      });
    });
    filteredEventTape.forEach((e, idx) => {
      derived.push({
        id: `agent-${e.timestamp || idx}-${e.message}`,
        severity: e.level === "error" ? "critical" : e.level === "success" ? "info" : "warn",
        source: "agent",
        message: e.message,
        timestamp: e.timestamp || new Date().toISOString(),
      });
    });
    const seen = new Set();
    return derived
      .filter((a) => !seen.has(a.id) && seen.add(a.id))
      .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp))
      .slice(0, 25);
  }, [killSwitch, risk.kill_switch_reason, risk.trading_allowed, risk.drawdown_pct, riskEvents?.events, filteredEventTape]);

  const unreadAlerts = alerts.filter((a) => !acknowledgedAlerts.includes(a.id));

  useEffect(() => {
    if (!uiSettings.soundAlerts) return;
    const hasCritical = unreadAlerts.some((a) => a.severity === "critical");
    if (hasCritical) {
      window?.navigator?.vibrate?.(120);
    }
  }, [unreadAlerts, uiSettings.soundAlerts]);

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
              onClick={() => setShowAlerts((p) => !p)}
              style={{
                background: "#0f172a", border: "1px solid #1e293b", borderRadius: 8, padding: "6px 10px",
                cursor: "pointer", color: unreadAlerts.length ? "#f59e0b" : "#64748b", position: "relative",
              }}
            >
              <Bell size={14} />
              {unreadAlerts.length > 0 && (
                <span style={{ position: "absolute", top: -4, right: -4, background: "#ef4444", color: "white", borderRadius: 999, fontSize: 9, minWidth: 16, lineHeight: "16px" }}>
                  {unreadAlerts.length}
                </span>
              )}
            </button>

            <button
              onClick={() => setSettingsOpen((p) => !p)}
              style={{
                background: "#0f172a", border: "1px solid #1e293b", borderRadius: 8, padding: "6px 10px",
                cursor: "pointer", color: "#64748b",
              }}
            >
              <Settings size={14} />
            </button>
            
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

        {showAlerts && (
          <div style={{ position: "absolute", right: 18, top: 62, width: 380, maxHeight: 420, overflowY: "auto", zIndex: 120, background: "#0b1220", border: "1px solid #1e293b", borderRadius: 10, padding: 12 }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8, fontSize: 11, color: "#94a3b8" }}>
              <span>Alerts Center</span>
              <span>{unreadAlerts.length} unread</span>
            </div>
            {alerts.length === 0 ? <div style={{ color: "#475569", fontSize: 11 }}>No active alerts.</div> : alerts.map((a) => {
              const isUnread = !acknowledgedAlerts.includes(a.id);
              const color = a.severity === "critical" ? "#ef4444" : a.severity === "warn" ? "#f59e0b" : "#22d3ee";
              return (
                <div key={a.id} style={{ border: "1px solid #1e293b", borderLeft: `3px solid ${color}`, borderRadius: 8, padding: "8px 10px", marginBottom: 8, opacity: isUnread ? 1 : 0.6 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
                    <span style={{ fontSize: 10, color }}>{a.severity.toUpperCase()} · {a.source}</span>
                    <span style={{ fontSize: 10, color: "#475569" }}>{new Date(a.timestamp).toLocaleTimeString("en-IN")}</span>
                  </div>
                  <div style={{ fontSize: 11, color: "#cbd5e1", marginBottom: 6 }}>{a.message}</div>
                  {isUnread && (
                    <button onClick={() => setAcknowledgedAlerts((prev) => [...prev, a.id])} style={{ fontSize: 10, color: "#22d3ee", background: "none", border: "none", cursor: "pointer", padding: 0 }}>
                      Acknowledge
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {settingsOpen && (
          <div style={{ position: "absolute", right: 410, top: 62, width: 280, zIndex: 120, background: "#0b1220", border: "1px solid #1e293b", borderRadius: 10, padding: 12 }}>
            <div style={{ fontSize: 11, color: "#94a3b8", marginBottom: 8 }}>Dashboard settings</div>
            {[
              { key: "compactMode", label: "Compact table density" },
              { key: "soundAlerts", label: "Sound on critical alerts" },
              { key: "colorBlindMode", label: "Color blind palette" },
            ].map((item) => (
              <label key={item.key} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8, fontSize: 11, color: "#cbd5e1" }}>
                {item.label}
                <input
                  type="checkbox"
                  checked={uiSettings[item.key]}
                  onChange={(e) => setUiSettings((prev) => ({ ...prev, [item.key]: e.target.checked }))}
                />
              </label>
            ))}
          </div>
        )}
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
              { id: "positions", label: "Positions", count: filteredPositions.length },
              { id: "orders", label: "Orders", count: filteredOrders.length },
              { id: "signals", label: "AI Signals", count: filteredDecisions.length },
              { id: "ticks", label: "Live Ticks", count: filteredTicksEntries.length },
              { id: "exposure", label: "Exposure", count: exposureData.byPosition.length },
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

          <div style={{ borderBottom: "1px solid rgba(255,255,255,0.05)", padding: "10px 20px", display: "grid", gridTemplateColumns: "1.5fr repeat(4, 1fr)", gap: 10 }}>
            <input
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="Search symbol, tag, commentary..."
              style={{ background: "#0f172a", border: "1px solid #1e293b", color: "#cbd5e1", borderRadius: 6, padding: "8px 10px", fontSize: 11 }}
            />
            <select value={strategyFilter} onChange={(e) => setStrategyFilter(e.target.value)} style={{ background: "#0f172a", border: "1px solid #1e293b", color: "#cbd5e1", borderRadius: 6, padding: "8px 10px", fontSize: 11 }}>
              {strategyOptions.map((s) => <option key={s} value={s}>{s === "all" ? "All strategies" : s.replace(/_/g, " ")}</option>)}
            </select>
            <select value={brokerFilter} onChange={(e) => setBrokerFilter(e.target.value)} style={{ background: "#0f172a", border: "1px solid #1e293b", color: "#cbd5e1", borderRadius: 6, padding: "8px 10px", fontSize: 11 }}>
              {brokerOptions.map((b) => <option key={b} value={b}>{b === "all" ? "All brokers" : b}</option>)}
            </select>
            <select value={timeRange} onChange={(e) => setTimeRange(e.target.value)} style={{ background: "#0f172a", border: "1px solid #1e293b", color: "#cbd5e1", borderRadius: 6, padding: "8px 10px", fontSize: 11 }}>
              <option value="15m">15m</option>
              <option value="1h">1h</option>
              <option value="today">Today</option>
            </select>
            <button onClick={() => { setSearchTerm(""); setStrategyFilter("all"); setBrokerFilter("all"); setTimeRange("today"); }} style={{ background: "#0ea5e915", border: "1px solid #0ea5e940", color: "#0ea5e9", borderRadius: 6, cursor: "pointer", fontSize: 11 }}>
              Clear filters
            </button>
          </div>

          <div style={{ padding: "0 20px 20px", minHeight: 300 }}>

            {activeTab === "positions" && (
              filteredPositions.length === 0 ? (
                <div style={{ textAlign: "center", padding: "60px 0", color: "#334155", fontSize: 13 }}>
                  {engineRunning ? "No open positions" : "Start the engine to see positions"}
                </div>
              ) : (
                <table style={{ width: "100%", borderCollapse: "collapse" }}>
                  <thead>
                    <tr>{["Symbol", "Side", "Qty", "Avg Price", "LTP", "P&L", "P&L %", "Broker"].map(h => (
                      <th key={h} style={{ padding: rowPadding, textAlign: "left", fontSize: 9, color: "#334155", letterSpacing: 2, textTransform: "uppercase", borderBottom: "1px solid #0f172a" }}>{h}</th>
                    ))}</tr>
                  </thead>
                  <tbody>
                    {filteredPositions.map((p, i) => (
                      <tr key={i} style={{ borderBottom: "1px solid #0a0f1a" }}>
                        <td style={{ padding: rowPadding, fontWeight: 700, fontSize: 13 }}>{p.symbol}</td>
                        <td style={{ padding: rowPadding }}><Badge text={p.side} /></td>
                        <td style={{ padding: rowPadding, fontFamily: "monospace", fontSize: 12 }}>{p.qty}</td>
                        <td style={{ padding: rowPadding, fontFamily: "monospace", fontSize: 12 }}>₹{(p.avg || 0).toLocaleString("en-IN")}</td>
                        <td style={{ padding: rowPadding, fontFamily: "monospace", fontSize: 12, color: "#94a3b8" }}>₹{(p.ltp || 0).toLocaleString("en-IN")}</td>
                        <td style={{ padding: rowPadding }}><PnLValue value={p.pnl || 0} size={13} /></td>
                        <td style={{ padding: rowPadding, fontSize: 12, color: (p.pnl || 0) >= 0 ? "#10b981" : "#ef4444" }}>
                          {(p.pnl || 0) >= 0 ? <ArrowUpRight size={12} style={{ display: "inline" }} /> : <ArrowDownRight size={12} style={{ display: "inline" }} />}
                          {Math.abs(((p.ltp - p.avg) / p.avg * 100) || 0).toFixed(2)}%
                        </td>
                        <td style={{ padding: rowPadding }}><Badge text={p.broker} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )
            )}

            {activeTab === "orders" && (
              filteredOrders.length === 0 ? (
                <div style={{ textAlign: "center", padding: "60px 0", color: "#334155", fontSize: 13 }}>
                  {engineRunning ? "No orders today" : "Start the engine to see orders"}
                </div>
              ) : (
                <table style={{ width: "100%", borderCollapse: "collapse" }}>
                  <thead>
                    <tr>{["Time", "Symbol", "Side", "Qty", "Price", "Avg", "Status", "Tag", "Exec"].map(h => (
                      <th key={h} style={{ padding: rowPadding, textAlign: "left", fontSize: 9, color: "#334155", letterSpacing: 2, textTransform: "uppercase", borderBottom: "1px solid #0f172a" }}>{h}</th>
                    ))}</tr>
                  </thead>
                  <tbody>
                    {filteredOrders.map((o, i) => {
                      const intended = Number(o.price || 0);
                      const avg = Number(o.average_price || 0);
                      const slippage = intended > 0 && avg > 0 ? ((avg - intended) / intended) * 100 : null;
                      const latency = o.placed_at && o.filled_at ? (new Date(o.filled_at).getTime() - new Date(o.placed_at).getTime()) / 1000 : null;
                      return (
                      <tr key={i} style={{ borderBottom: "1px solid #0a0f1a" }}>
                        <td style={{ padding: rowPadding, fontFamily: "monospace", fontSize: 11, color: "#475569" }}>{new Date(o.placed_at).toLocaleTimeString("en-IN")}</td>
                        <td style={{ padding: rowPadding, fontWeight: 700, fontSize: 12 }}>{o.symbol}</td>
                        <td style={{ padding: rowPadding }}><Badge text={o.side} /></td>
                        <td style={{ padding: rowPadding, fontFamily: "monospace", fontSize: 12 }}>{o.quantity}</td>
                        <td style={{ padding: rowPadding, fontFamily: "monospace", fontSize: 12 }}>{o.price ? `₹${o.price.toLocaleString("en-IN")}` : "MKT"}</td>
                        <td style={{ padding: rowPadding, fontFamily: "monospace", fontSize: 12, color: "#64748b" }}>{o.average_price ? `₹${o.average_price.toLocaleString("en-IN")}` : "—"}</td>
                        <td style={{ padding: rowPadding }}><Badge text={o.status} /></td>
                        <td style={{ padding: rowPadding, fontSize: 10, color: "#475569" }}>{o.tag || "—"}</td>
                        <td style={{ padding: rowPadding, fontSize: 10, color: "#64748b" }} title={`Slippage: ${slippage === null ? "n/a" : `${slippage.toFixed(2)}%`} · Fill latency: ${latency === null ? "n/a" : `${latency.toFixed(1)}s`}`}>
                          {slippage === null ? "—" : `${slippage.toFixed(2)}%`} · {latency === null ? "—" : `${latency.toFixed(1)}s`}
                        </td>
                      </tr>
                    )})}
                  </tbody>
                </table>
              )
            )}

            {activeTab === "signals" && (
              <div>
                <div style={{
                  background: "#0a0f1a", border: "1px solid #1e293b", borderRadius: 10,
                  padding: "14px 16px", marginBottom: 10,
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
                    <span style={{ fontSize: 10, color: "#334155", letterSpacing: 1.2, textTransform: "uppercase" }}>
                      Live AI Reasoning
                    </span>
                    {isPreviewMode && <Badge text="preview_layout" />}
                    <span style={{ marginLeft: "auto", fontFamily: "monospace", fontSize: 10, color: "#475569" }}>
                      cycle {liveCycleId}
                    </span>
                  </div>

                  <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr", gap: 12, marginBottom: 12 }}>
                    <div>
                      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8, flexWrap: "wrap" }}>
                        <span style={{ fontSize: 11, color: "#94a3b8" }}>Stage:</span>
                        <Badge text={agentStatus?.stage || "collecting_context"} />
                        {selectedStrategy && <><span style={{ fontSize: 11, color: "#94a3b8" }}>Style:</span><Badge text={selectedStrategy} /></>}
                      </div>
                      <div style={{ width: "100%", height: 8, borderRadius: 99, background: "#111827", overflow: "hidden" }}>
                        <div style={{ width: `${Math.max(8, progressPct)}%`, height: "100%", background: "linear-gradient(90deg,#22d3ee,#6366f1)", transition: "width .25s" }} />
                      </div>
                      <div style={{ marginTop: 5, fontSize: 10, color: "#64748b", display: "flex", justifyContent: "space-between" }}>
                        <span>{progressPct}% complete</span>
                        <span>{agentStatus?.last_cycle_duration_ms ? `${agentStatus.last_cycle_duration_ms} ms` : "Calculating..."}</span>
                      </div>
                    </div>
                    <div style={{ border: "1px solid #1f2937", borderRadius: 8, padding: "10px 12px", background: "#0b1220" }}>
                      <div style={{ fontSize: 10, color: "#64748b", marginBottom: 4 }}>Now processing</div>
                      <div style={{ fontSize: 11, color: "#e2e8f0" }}>
                        {isPreviewMode ? "AI is collecting market context..." : "Evaluating signal confidence and risk gates."}
                      </div>
                      <div style={{ fontSize: 10, color: "#475569", marginTop: 6 }}>
                        Last update: {new Date().toLocaleTimeString("en-IN")}
                      </div>
                    </div>
                  </div>

                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10 }}>
                    {filteredSignals.map((s, idx) => {
                      const confidence = Math.round((Number(s.confidence || 0) || 0) * 100);
                      const riskStatus = s.risk_status || (s.action === "NO_ACTION" ? "rejected" : "approved");
                      return (
                        <div key={`${s.symbol}-${idx}`} style={{ border: "1px solid #1e293b", borderRadius: 9, padding: "10px 12px", background: "#0b1220" }}>
                          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
                            <span style={{ fontWeight: 700, fontSize: 12 }}>{s.symbol}</span>
                            <Badge text={s.action || "NO_ACTION"} />
                            <span style={{ marginLeft: "auto" }}><Badge text={s.strategy || "unknown"} /></span>
                          </div>
                          <div style={{ fontSize: 10, color: "#64748b", marginBottom: 4 }}>Confidence {confidence}%</div>
                          <div style={{ width: "100%", height: 6, borderRadius: 99, background: "#1f2937", overflow: "hidden", marginBottom: 6 }}>
                            <div style={{ width: `${Math.max(4, confidence)}%`, height: "100%", background: confidence >= 65 ? "#10b981" : "#f59e0b" }} />
                          </div>
                          <div style={{ fontFamily: "monospace", fontSize: 10, color: "#94a3b8", lineHeight: 1.5 }}>
                            Entry: {s.entry_price ? `₹${Number(s.entry_price).toLocaleString("en-IN")}` : "—"} · SL: {s.stop_loss ? `₹${Number(s.stop_loss).toLocaleString("en-IN")}` : "—"} · Tgt: {s.target ? `₹${Number(s.target).toLocaleString("en-IN")}` : "—"}
                          </div>
                          <div style={{ fontSize: 10, color: "#cbd5e1", marginTop: 6 }}>{s.rationale || "No rationale available."}</div>
                          <div style={{ marginTop: 8 }}>
                            <Badge text={riskStatus === "approved" ? "risk_passed" : "risk_rejected"} />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
                
                <div style={{
                  background: "#0a0f1a", border: "1px solid #1e293b", borderRadius: 10,
                  padding: "12px 14px", marginBottom: 10,
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                    <span style={{ fontSize: 10, color: "#334155", letterSpacing: 1.2, textTransform: "uppercase" }}>
                      AI Pipeline
                    </span>
                    {[
                      "collecting_context",
                      "calling_model",
                      "risk_checks",
                      "placing_orders",
                      "decision_complete",
                    ].map((stage) => {
                      const isCurrent = agentStatus?.stage === stage;
                      const isDone = stage === "decision_complete" && agentStatus?.stage === "decision_complete";
                      return (
                        <span
                          key={stage}
                          style={{
                            borderRadius: 999,
                            padding: "3px 8px",
                            fontSize: 10,
                            border: `1px solid ${isCurrent ? "#22d3ee55" : isDone ? "#10b98155" : "#334155"}`,
                            color: isCurrent ? "#22d3ee" : isDone ? "#10b981" : "#64748b",
                            background: isCurrent ? "#083344" : isDone ? "#052e27" : "transparent",
                          }}
                        >
                          {stage.replace(/_/g, " ")}
                        </span>
                      );
                    })}
                    <span style={{ marginLeft: "auto", fontSize: 10, color: "#475569" }}>
                      Last cycle: {agentStatus?.last_cycle_duration_ms ? `${agentStatus.last_cycle_duration_ms} ms` : "—"}
                    </span>
                  </div>

                  <div style={{ marginTop: 10, display: "flex", gap: 12, overflowX: "auto", whiteSpace: "nowrap", paddingBottom: 2 }}>
                    {filteredEventTape.length === 0 ? (
                      <span style={{ fontSize: 11, color: "#334155" }}>Waiting for AI events…</span>
                    ) : filteredEventTape.slice().reverse().map((e, idx) => (
                      <span key={idx} style={{
                        fontSize: 11,
                        color: e.level === "error" ? "#ef4444" : e.level === "success" ? "#10b981" : "#64748b",
                      }}>
                        {new Date(e.timestamp).toLocaleTimeString("en-IN")} · {e.message}
                      </span>
                    ))}
                  </div>
                </div>
      
                {filteredDecisions.length === 0 ? (
                  <div style={{ textAlign: "center", padding: "60px 0", color: "#334155", fontSize: 13 }}>
                    {engineRunning ? "Waiting for first AI decision..." : "Start the engine to see AI decisions"}
                  </div>
                ) : filteredDecisions.slice().reverse().map((d, i) => (
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
                    {(d.market_commentary || d.commentary) && (
                      <div style={{ fontSize: 11, color: "#94a3b8", marginBottom: 8 }}>
                        {d.market_commentary || d.commentary}
                      </div>
                    )}
                    <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                      {d.risk_assessment && <Badge text={d.risk_assessment} />}
                      {d.session_recommendation && <Badge text={d.session_recommendation} />}
                      {d.rejection_breakdown && Object.keys(d.rejection_breakdown).length > 0 && (
                        <span style={{ fontSize: 10, color: "#475569" }}>
                          Rejections: {Object.entries(d.rejection_breakdown).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(" · ")}
                        </span>
                      )}
                    </div>  
                  </div>
                ))}
              </div>
            )}

            {activeTab === "ticks" && (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 10, paddingTop: 14 }}>
                {filteredTicksEntries.length === 0 ? (
                  <div style={{ gridColumn: "1/-1", textAlign: "center", padding: "60px 0", color: "#334155", fontSize: 13 }}>
                    {engineRunning ? "Waiting for tick data..." : "Start the engine to see live ticks"}
                  </div>
                ) : filteredTicksEntries.map(([symbol, ltp]) => {
                  const current = Number(ltp || 0);
                  const prev = Number(prevTicks[symbol] || current || 0);
                  const delta = current - prev;
                  const deltaPct = prev ? (delta / prev) * 100 : 0;
                  const isUp = delta > 0;
                  const series = tickHistory[symbol] || [];
                  const min = Math.min(...series, current || 0);
                  const max = Math.max(...series, current || 0);
                  const range = max - min || 1;
                  const points = series.map((v, i) => `${(i / Math.max(series.length - 1, 1)) * 100},${22 - ((v - min) / range) * 18}`).join(" ");
                  return (
                  <div key={symbol} style={{
                    background: "#0a0f1a", border: "1px solid #1e293b", borderRadius: 8, padding: "12px 14px",
                    boxShadow: delta > 0 ? "0 0 12px #10b98122" : delta < 0 ? "0 0 12px #ef444422" : "none",
                  }}>
                    <div style={{ fontSize: 11, fontWeight: 700 }}>{symbol}</div>
                    <div style={{ fontFamily: "monospace", fontSize: 16, fontWeight: 700, color: isUp ? "#10b981" : delta < 0 ? "#ef4444" : "#e2e8f0", marginTop: 4 }}>
                      ₹{current ? current.toLocaleString("en-IN") : "—"}
                    </div>
                    <div style={{ fontFamily: "monospace", fontSize: 10, color: isUp ? "#10b981" : delta < 0 ? "#ef4444" : "#64748b", marginTop: 2 }}>
                      {delta > 0 ? "▲" : delta < 0 ? "▼" : "•"} {Math.abs(delta).toFixed(2)} ({Math.abs(deltaPct).toFixed(2)}%)
                    </div>
                    <div style={{ marginTop: 6, height: 24, borderRadius: 5, background: "#0f172a", overflow: "hidden" }}>
                      <svg viewBox="0 0 100 24" preserveAspectRatio="none" width="100%" height="24">
                        <polyline
                          fill="none"
                          stroke={isUp ? "#10b981" : delta < 0 ? "#ef4444" : "#22d3ee"}
                          strokeWidth="2"
                          points={points || "0,12 100,12"}
                        />
                      </svg>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 2 }}>
                      <Pulse active={true} color="#0ea5e9" />
                      <span style={{ fontSize: 9, color: "#334155" }}>LIVE</span>
                    </div>
                  </div>
                )})}
              </div>
            )}

            {activeTab === "exposure" && (
              <div style={{ paddingTop: 14 }}>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 14 }}>
                  <StatCard label="Gross Exposure" value={`₹${Math.round(exposureData.total).toLocaleString("en-IN")}`} sub="Across filtered positions" color="#22d3ee" />
                  <StatCard label="Long Exposure" value={`${exposureData.longPct.toFixed(1)}%`} sub="BUY side" color="#10b981" />
                  <StatCard label="Short Exposure" value={`${exposureData.shortPct.toFixed(1)}%`} sub="SELL/SHORT side" color="#ef4444" />
                  <StatCard label="Top Position" value={`${(exposureData.byPosition[0]?.weightPct || 0).toFixed(1)}%`} sub={exposureData.concentrationFlag ? "⚠️ High concentration" : "Within limit"} color={exposureData.concentrationFlag ? "#ef4444" : "#6366f1"} />
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                  <div style={{ background: "#0a0f1a", border: "1px solid #1e293b", borderRadius: 10, padding: "12px 14px" }}>
                    <div style={{ fontSize: 11, color: "#64748b", marginBottom: 8 }}>Top Position Weights</div>
                    <ResponsiveContainer width="100%" height={220}>
                      <BarChart data={exposureData.byPosition}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#0f172a" />
                        <XAxis dataKey="symbol" tick={{ fill: "#334155", fontSize: 9 }} tickLine={false} />
                        <YAxis tick={{ fill: "#334155", fontSize: 9 }} tickLine={false} tickFormatter={(v) => `${v.toFixed(0)}%`} />
                        <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #1e293b" }} formatter={(v) => [`${v.toFixed(2)}%`, "Weight"]} />
                        <Bar dataKey="weightPct" fill="#6366f1" radius={[3, 3, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>

                  <div style={{ background: "#0a0f1a", border: "1px solid #1e293b", borderRadius: 10, padding: "12px 14px" }}>
                    <div style={{ fontSize: 11, color: "#64748b", marginBottom: 8 }}>Sector Allocation</div>
                    <ResponsiveContainer width="100%" height={220}>
                      <BarChart data={exposureData.bySector.slice(0, 7)}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#0f172a" />
                        <XAxis dataKey="sector" tick={{ fill: "#334155", fontSize: 9 }} tickLine={false} />
                        <YAxis tick={{ fill: "#334155", fontSize: 9 }} tickLine={false} tickFormatter={(v) => `${v.toFixed(0)}%`} />
                        <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #1e293b" }} formatter={(v) => [`${v.toFixed(2)}%`, "Weight"]} />
                        <Bar dataKey="weightPct" fill="#22d3ee" radius={[3, 3, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </div>
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

                <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginTop: 16 }}>
                  {[
                    { label: "Fill Rate", value: `${executionMetrics.fillRate.toFixed(1)}%` },
                    { label: "Avg Slippage", value: executionMetrics.avgSlippage === null ? "—" : `${executionMetrics.avgSlippage.toFixed(2)}%` },
                    { label: "Avg Fill Latency", value: executionMetrics.avgLatency === null ? "—" : `${executionMetrics.avgLatency.toFixed(1)}s` },
                    { label: "Rejections", value: executionMetrics.rejectedCount },
                  ].map((m) => (
                    <div key={m.label} style={{ background: "#0a0f1a", border: "1px solid #1e293b", borderRadius: 8, padding: "12px 14px", textAlign: "center" }}>
                      <div style={{ fontSize: 9, color: "#334155", letterSpacing: 1.2, textTransform: "uppercase", marginBottom: 6 }}>{m.label}</div>
                      <div style={{ fontFamily: "monospace", fontWeight: 700, fontSize: 15 }}>{m.value}</div>
                    </div>
                  ))}
                </div>

                {executionMetrics.topRejections.length > 0 && (
                  <div style={{ marginTop: 10, fontSize: 11, color: "#64748b" }}>
                    Top rejection reasons: {executionMetrics.topRejections.map(([reason, count]) => `${reason} (${count})`).join(" · ")}
                  </div>
                )}

                <div style={{ marginTop: 18, background: "#0a0f1a", border: "1px solid #1e293b", borderRadius: 10, padding: "12px 14px" }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
                    <div style={{ fontWeight: 600, fontSize: 12, color: "#94a3b8" }}>Strategy Leaderboard</div>
                    <div style={{ fontSize: 10, color: "#475569" }}>Click strategy badge above to filter</div>
                  </div>
                  {strategyLeaderboard.length === 0 ? (
                    <div style={{ fontSize: 11, color: "#475569" }}>No strategy stats for selected filters.</div>
                  ) : (
                    <>
                      <ResponsiveContainer width="100%" height={180}>
                        <BarChart data={strategyLeaderboard.slice(0, 6)}>
                          <CartesianGrid strokeDasharray="3 3" stroke="#0f172a" />
                          <XAxis dataKey="strategy" tick={{ fill: "#334155", fontSize: 9 }} tickLine={false} />
                          <YAxis tick={{ fill: "#334155", fontSize: 9 }} tickLine={false} tickFormatter={(v) => `₹${(v / 1000).toFixed(0)}K`} />
                          <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #1e293b" }} formatter={(v) => [`₹${Number(v).toLocaleString("en-IN")}`, "Net P&L"]} />
                          <ReferenceLine y={0} stroke="#1e293b" />
                          <Bar dataKey="netPnl" fill="#a78bfa" radius={[3, 3, 0, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                      <table style={{ width: "100%", borderCollapse: "collapse", marginTop: 8 }}>
                        <thead>
                          <tr>{["Strategy", "Signals", "Execution %", "Avg Confidence", "Net P&L"].map((h) => (
                            <th key={h} style={{ padding: "8px 6px", textAlign: "left", fontSize: 9, color: "#334155", letterSpacing: 1.2, textTransform: "uppercase", borderBottom: "1px solid #0f172a" }}>{h}</th>
                          ))}</tr>
                        </thead>
                        <tbody>
                          {strategyLeaderboard.slice(0, 8).map((row) => (
                            <tr key={row.strategy} style={{ borderBottom: "1px solid #0a0f1a" }}>
                              <td style={{ padding: "8px 6px" }}><button onClick={() => setStrategyFilter(row.strategy)} style={{ background: "none", border: "none", color: "#22d3ee", cursor: "pointer", padding: 0 }}>{row.strategy.replace(/_/g, " ")}</button></td>
                              <td style={{ padding: "8px 6px", fontFamily: "monospace", fontSize: 11 }}>{row.signals}</td>
                              <td style={{ padding: "8px 6px", fontFamily: "monospace", fontSize: 11 }}>{row.executionRate.toFixed(1)}%</td>
                              <td style={{ padding: "8px 6px", fontFamily: "monospace", fontSize: 11 }}>{row.avgConfidence.toFixed(1)}%</td>
                              <td style={{ padding: "8px 6px", fontFamily: "monospace", fontSize: 11, color: row.netPnl >= 0 ? "#10b981" : "#ef4444" }}>₹{Math.round(row.netPnl).toLocaleString("en-IN")}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </>
                  )}
                </div>
    
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

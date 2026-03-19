import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import {
  AreaChart, Area, BarChart, Bar, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  ReferenceLine, Cell, ComposedChart,
} from "recharts";
import {
  TrendingUp, TrendingDown, Activity, Shield, Zap,
  AlertTriangle, Power, RefreshCw, Target, DollarSign,
  Cpu, Radio, Database, BarChart2, Bell, Settings,
  Eye, Layers, GitBranch, Clock, CheckCircle, XCircle,
  AlertCircle, Wifi, WifiOff, List, BarChart as BarChartIcon,
  ChevronRight, Sun, Moon, Menu, X, Play, Square,
} from "lucide-react";

// ─── CONFIG ──────────────────────────────────────────────────────────────────
const isLocalHostname = ["localhost", "127.0.0.1"].includes(window.location.hostname);
const configuredApiBase = (import.meta.env.VITE_API_BASE || "").trim();
const API_BASE = configuredApiBase || (isLocalHostname ? "" : "https://agentic-trading-bot-188e.onrender.com");
const WS_URL = (import.meta.env.VITE_WS_URL || "").trim() || (API_BASE ? API_BASE.replace(/^http/, "ws") : window.location.origin.replace(/^http/, "ws")) + "/ws";
const API_BASE_SOURCE = configuredApiBase ? "VITE_API_BASE" : (isLocalHostname ? "local Vite proxy (/api -> http://localhost:8000)" : "production fallback");
const IS_REMOTE_API = Boolean(API_BASE) && !/^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/i.test(API_BASE);
const REPLAY_POLL_TIMEOUT_SECONDS = Number(import.meta.env.VITE_REPLAY_POLL_TIMEOUT_SECONDS ?? 1800);
const SIM_SOURCE = "NSE historical candles + AI decision/risk pipeline";

const tickPrice = (v) => {
  if (v && typeof v === "object") return Number(v.price || 0);
  return Number(v || 0);
};

const extractErrorMessage = (value) => {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (value instanceof Error) return extractErrorMessage(value.message);
  if (Array.isArray(value)) return value.map(extractErrorMessage).filter(Boolean).join("; ");
  if (typeof value === "object") {
    if (value.detail !== undefined) return extractErrorMessage(value.detail);
    if (value.error !== undefined) return extractErrorMessage(value.error);
    if (value.message !== undefined) return extractErrorMessage(value.message);
    if (value.msg) {
      const loc = Array.isArray(value.loc) ? value.loc.join(".") : value.loc;
      return loc ? `${loc}: ${value.msg}` : String(value.msg);
    }
    try { return JSON.stringify(value); } catch { return String(value); }
  }
  return String(value);
};

const toUiError = (err, fallback = "Unexpected error") => {
  const msg = extractErrorMessage(err);
  return msg && msg.trim() ? msg : fallback;
};

const toIsoDateOrNull = (value) => {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  return /^\d{4}-\d{2}-\d{2}$/.test(trimmed) ? trimmed : null;
};

const normalizeSymbols = (value) => {
  if (Array.isArray(value)) return value.map(s => String(s || "").trim().toUpperCase()).filter(Boolean);
  if (typeof value !== "string") return [];
  return value.split(",").map(s => s.trim().toUpperCase()).filter(Boolean);
};

const formatRupees = (value) => `₹${Number(value || 0).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;

// ─── THEME ───────────────────────────────────────────────────────────────────
const DARK = {
  bg: "#03060d", bgAlt: "#060b14", surface: "#08111e", card: "#0a1525",
  cardHover: "#0d1a2e", border: "#112238", borderLight: "#163050",
  text: "#d6e8ff", textSub: "#7aa0c4", textMuted: "#3d6080", textDim: "#1a3050",
  accent: "#00e5ff", accentDim: "#00e5ff12", accentGlow: "#00e5ff40",
  green: "#00ff88", greenDim: "#00ff8810", red: "#ff3355", redDim: "#ff335510",
  amber: "#ffaa00", amberDim: "#ffaa0010", blue: "#4d9fff", blueDim: "#4d9fff12",
  purple: "#c77dff", cyan: "#00e5ff",
  shadow: "0 2px 8px rgba(0,0,0,0.8), inset 0 1px 0 rgba(255,255,255,0.03)",
  shadowHover: "0 8px 32px rgba(0,0,0,0.6), 0 0 0 1px rgba(0,229,255,0.1)",
  gridLine: "rgba(0,229,255,0.04)", scanline: "rgba(0,229,255,0.015)",
};

const LIGHT = {
  bg: "#f5f7fa", bgAlt: "#edf0f5", surface: "#ffffff", card: "#ffffff",
  cardHover: "#f8fafc", border: "#dce5f0", borderLight: "#c8d8e8",
  text: "#0a1628", textSub: "#3a5a80", textMuted: "#7a9ab8", textDim: "#c0d4e8",
  accent: "#006688", accentDim: "#00668812", accentGlow: "#00668830",
  green: "#007744", greenDim: "#00774410", red: "#cc2244", redDim: "#cc224410",
  amber: "#cc7700", amberDim: "#cc770010", blue: "#1155cc", blueDim: "#1155cc12",
  purple: "#7722aa", cyan: "#005577",
  shadow: "0 1px 4px rgba(0,0,0,0.08), 0 0 0 1px rgba(0,0,0,0.04)",
  shadowHover: "0 8px 24px rgba(0,0,0,0.12)",
  gridLine: "rgba(0,0,0,0.04)", scanline: "transparent",
};

// ─── HOOKS ───────────────────────────────────────────────────────────────────
function useAPI(endpoint, interval = null) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const fetch_ = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}${endpoint}`);
      if (!res.ok) { setData(null); setLoading(false); return; }
      setData(await res.json());
    } catch { setData(null); }
    finally { setLoading(false); }
  }, [endpoint]);
  useEffect(() => {
    fetch_();
    if (interval) { const t = setInterval(fetch_, interval); return () => clearInterval(t); }
  }, [fetch_, interval]);
  return { data, loading, refetch: fetch_ };
}

function useWebSocket() {
  const [liveData, setLiveData] = useState(null);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);
  const reconnectRef = useRef(null);
  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;
    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;
    ws.onopen = () => { setConnected(true); clearTimeout(reconnectRef.current); };
    ws.onmessage = (e) => { try { setLiveData(JSON.parse(e.data)); } catch {} };
    ws.onclose = () => { setConnected(false); reconnectRef.current = setTimeout(connect, 3000); };
    ws.onerror = () => ws.close();
  }, []);
  useEffect(() => { connect(); return () => { clearTimeout(reconnectRef.current); wsRef.current?.close(); }; }, [connect]);
  return { liveData, connected };
}

function useBreakpoint() {
  const [w, setW] = useState(window.innerWidth);
  useEffect(() => {
    const h = () => setW(window.innerWidth);
    window.addEventListener("resize", h);
    return () => window.removeEventListener("resize", h);
  }, []);
  return { isMobile: w < 768, isTablet: w >= 768 && w < 1200, isDesktop: w >= 1200, width: w };
}

// ─── DESIGN SYSTEM ───────────────────────────────────────────────────────────
const ACTION_COLORS_FN = (T) => ({
  BUY: T.green, SELL: T.red, SHORT: T.amber, COVER: T.blue,
  NO_ACTION: T.textMuted, HOLD: T.textMuted,
});
const STRATEGY_COLORS_FN = (T) => ({
  momentum: T.purple, mean_reversion: T.amber,
  options_selling: "#ff6eb4", breakout: T.cyan, scalping: T.blue,
});
const SIGNAL_COLORS_FN = (T) => ({
  strong_buy: T.green, buy: T.green, neutral: T.amber, sell: T.red, strong_sell: T.red,
});

// ─── PRIMITIVES ──────────────────────────────────────────────────────────────
const Pill = ({ label, color, T }) => (
  <span style={{
    background: `${color}18`, color, border: `1px solid ${color}45`,
    borderRadius: 2, padding: "2px 7px", fontSize: 9, fontWeight: 800,
    letterSpacing: 1.2, textTransform: "uppercase", whiteSpace: "nowrap",
    fontFamily: "'Share Tech Mono', monospace", boxShadow: `0 0 6px ${color}20`,
  }}>{(label || "").replace(/_/g, " ")}</span>
);

const StatusDot = ({ active, color }) => (
  <span style={{
    display: "inline-block", width: 7, height: 7, borderRadius: "50%",
    background: active ? color : "currentColor",
    boxShadow: active ? `0 0 0 2px ${color}25, 0 0 10px ${color}80` : "none",
    opacity: active ? 1 : 0.25,
    animation: active ? "pulse-dot 2s ease-in-out infinite" : "none",
  }} />
);

const Delta = ({ value, size = 12, showPrefix = true }) => {
  const up = value >= 0;
  return (
    <span style={{
      color: up ? "#00ff88" : "#ff3355", fontFamily: "'Share Tech Mono', monospace",
      fontWeight: 700, fontSize: size,
      textShadow: up ? "0 0 8px #00ff8860" : "0 0 8px #ff335560",
    }}>
      {showPrefix && (up ? "▲" : "▼")}{typeof value === "number" ? value.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : value}
    </span>
  );
};

const Mono = ({ children, size = 12, color, weight = 600 }) => (
  <span style={{ fontFamily: "'Share Tech Mono', monospace", fontSize: size, fontWeight: weight, color }}>{children}</span>
);

const ProgressBar = ({ value, max, color, height = 3 }) => (
  <div style={{ width: "100%", height, background: "rgba(255,255,255,0.04)", borderRadius: 0, overflow: "hidden", position: "relative" }}>
    <div style={{
      width: `${Math.min((value / max) * 100, 100)}%`, height: "100%",
      background: `linear-gradient(90deg, ${color}80, ${color})`, borderRadius: 0,
      transition: "width 0.5s cubic-bezier(0.4,0,0.2,1)",
      boxShadow: `0 0 8px ${color}60`, position: "relative",
    }}>
      <div style={{ position: "absolute", right: 0, top: 0, width: 2, height: "100%", background: color, boxShadow: `0 0 4px ${color}` }} />
    </div>
  </div>
);

const Sparkline = ({ data, color, height = 28 }) => {
  if (!data?.length) return <div style={{ height }} />;
  const min = Math.min(...data), max = Math.max(...data), range = max - min || 1;
  const w = 80, h = height;
  const pts = data.map((v, i) => `${(i / Math.max(data.length - 1, 1)) * w},${h - ((v - min) / range) * h * 0.8 - h * 0.1}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" width="100%" height={h}>
      <defs>
        <linearGradient id={`sg-${color.replace("#", "")}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.4" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <polyline fill={`url(#sg-${color.replace("#", "")})`} stroke="none" points={`0,${h} ${pts} ${w},${h}`} />
      <polyline fill="none" stroke={color} strokeWidth="1.5" points={pts} strokeLinejoin="round" strokeLinecap="round" style={{ filter: `drop-shadow(0 0 3px ${color})` }} />
    </svg>
  );
};

const Card = ({ children, T, style = {}, className, accent }) => (
  <div className={className} style={{
    background: T.card, border: `1px solid ${T.border}`, borderRadius: 4,
    boxShadow: T.shadow, position: "relative", overflow: "hidden", ...style,
  }}>
    {accent && <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 1, background: `linear-gradient(90deg, transparent, ${accent}80, transparent)` }} />}
    {children}
  </div>
);

const CardHeader = ({ title, subtitle, right, T, accent }) => (
  <div style={{
    display: "flex", alignItems: "center", justifyContent: "space-between",
    padding: "11px 16px", borderBottom: `1px solid ${T.border}`, gap: 12,
    background: `linear-gradient(90deg, ${T.bgAlt}cc, transparent)`,
  }}>
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      {accent && <div style={{ width: 2, height: 14, background: accent, boxShadow: `0 0 6px ${accent}`, borderRadius: 1 }} />}
      <div>
        <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: 2, color: T.textMuted, textTransform: "uppercase", fontFamily: "'Share Tech Mono', monospace" }}>{title}</div>
        {subtitle && <div style={{ fontSize: 9, color: T.textDim, marginTop: 1, letterSpacing: 0.5 }}>{subtitle}</div>}
      </div>
    </div>
    {right && <div style={{ flexShrink: 0 }}>{right}</div>}
  </div>
);

const StatTile = ({ label, value, sub, delta, color, icon: Icon, T }) => (
  <div style={{
    background: T.card, border: `1px solid ${T.border}`, borderRadius: 4,
    padding: "16px", position: "relative", overflow: "hidden", boxShadow: T.shadow, transition: "all 0.2s",
  }}>
    <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 1, background: `linear-gradient(90deg, transparent, ${color}80, transparent)` }} />
    <div style={{ position: "absolute", bottom: 0, right: 0, width: 60, height: 60, background: `radial-gradient(circle, ${color}08 0%, transparent 70%)`, pointerEvents: "none" }} />
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", position: "relative" }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 8.5, fontWeight: 700, letterSpacing: 2, textTransform: "uppercase", color: T.textMuted, marginBottom: 10, fontFamily: "'Share Tech Mono', monospace" }}>{label}</div>
        <div style={{ fontSize: 22, fontWeight: 700, color: T.text, fontFamily: "'Share Tech Mono', monospace", letterSpacing: -0.5, lineHeight: 1, textShadow: `0 0 20px ${color}30` }}>{value}</div>
        {sub && <div style={{ fontSize: 9.5, color: T.textMuted, marginTop: 6, letterSpacing: 0.3 }}>{sub}</div>}
        {delta !== undefined && <div style={{ marginTop: 4 }}><Delta value={delta} size={10} /></div>}
      </div>
      {Icon && (
        <div style={{ background: `${color}10`, border: `1px solid ${color}25`, borderRadius: 3, padding: 9, flexShrink: 0, boxShadow: `0 0 12px ${color}15` }}>
          <Icon size={15} color={color} style={{ filter: `drop-shadow(0 0 4px ${color})` }} />
        </div>
      )}
    </div>
  </div>
);

const tooltipStyle = (T) => ({
  contentStyle: { background: T.surface, border: `1px solid ${T.border}`, borderRadius: 4, fontSize: 10, color: T.text, boxShadow: T.shadowHover, fontFamily: "'Share Tech Mono', monospace" },
  labelStyle: { color: T.textMuted, letterSpacing: 1 },
});

// ─── CANDLE CHART COMPONENT ──────────────────────────────────────────────────
function CandleChart({ candles, T, height = 200 }) {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !candles?.length) return;
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const W = canvas.offsetWidth;
    const H = height;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, W, H);

    const slice = candles.slice(-40);
    const highs = slice.map(c => c.high), lows = slice.map(c => c.low);
    const maxP = Math.max(...highs), minP = Math.min(...lows);
    const range = maxP - minP || 1;
    const padX = 8, padY = 12;
    const areaW = W - padX * 2, areaH = (H - padY * 2) * 0.8;
    const volH = (H - padY * 2) * 0.15;
    const n = slice.length;
    const bw = Math.max(1.5, areaW / n - 1.5);
    const gap = areaW / n;
    const py = v => padY + areaH * (1 - (v - minP) / range);
    const maxVol = Math.max(...slice.map(c => c.volume || 1));

    // Grid lines
    ctx.strokeStyle = T.gridLine;
    ctx.lineWidth = 0.5;
    for (let i = 0; i <= 4; i++) {
      const y = padY + areaH * i / 4;
      ctx.beginPath(); ctx.moveTo(padX, y); ctx.lineTo(W - padX, y); ctx.stroke();
      const val = maxP - range * i / 4;
      ctx.fillStyle = T.textMuted;
      ctx.font = `7px 'Share Tech Mono', monospace`;
      ctx.fillText(val.toFixed(0), padX + 2, y - 2);
    }

    // Candles
    slice.forEach((c, i) => {
      const x = padX + i * gap + gap / 2;
      const isUp = c.close >= c.open;
      const col = isUp ? T.green : T.red;
      ctx.strokeStyle = col + "90";
      ctx.lineWidth = 0.8;
      ctx.beginPath();
      ctx.moveTo(x, py(c.high));
      ctx.lineTo(x, py(c.low));
      ctx.stroke();
      const bodyTop = py(Math.max(c.open, c.close));
      const bodyBot = py(Math.min(c.open, c.close));
      const bH = Math.max(1, bodyBot - bodyTop);
      ctx.fillStyle = isUp ? col + "bb" : col + "99";
      if (i === n - 1) { ctx.shadowColor = col; ctx.shadowBlur = 6; }
      ctx.fillRect(x - bw / 2, bodyTop, bw, bH);
      ctx.shadowBlur = 0;
      // Volume bar
      const vh = ((c.volume || 0) / maxVol) * volH;
      ctx.fillStyle = col + "25";
      ctx.fillRect(x - bw / 2, H - padY - vh, bw, vh);
    });
  }, [candles, T, height]);

  return <canvas ref={canvasRef} style={{ width: "100%", height, display: "block" }} />;
}

// ─── SIMULATOR CANDLE CHART ──────────────────────────────────────────────────
function SimCandleChart({ symbol, candle, priceData, T }) {
  const candles = useMemo(() => {
    if (!priceData?.[symbol]) return [];
    const prices = priceData[symbol];
    const end = Math.min(candle + 1, prices.length);
    const start = Math.max(0, end - 35);
    const slice = prices.slice(start, end);
    return slice.map((c, i) => {
      const prev = i > 0 ? slice[i - 1] : c;
      const range = c * 0.012;
      return {
        open: prev * (1 + (Math.random() - 0.5) * 0.004),
        high: Math.max(prev, c) + Math.random() * range * 0.5,
        low: Math.min(prev, c) - Math.random() * range * 0.5,
        close: c,
        volume: Math.round(50000 + Math.random() * 200000),
      };
    });
  }, [symbol, candle, priceData]);

  return <CandleChart candles={candles} T={T} height={180} />;
}

// ─── AI THOUGHT STREAM ───────────────────────────────────────────────────────
function ThoughtStream({ thoughts, isThinking, T }) {
  const ref = useRef(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = 0;
  }, [thoughts]);

  return (
    <div ref={ref} style={{ maxHeight: 200, overflowY: "auto" }}>
      {isThinking && (
        <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "6px 0", borderBottom: `1px solid ${T.border}20` }}>
          <div style={{ display: "flex", gap: 3, alignItems: "center" }}>
            {[0, 1, 2].map(i => (
              <span key={i} style={{
                width: 4, height: 4, borderRadius: "50%", background: T.accent,
                animation: `typing-bounce 1.2s infinite ${i * 0.2}s`,
                display: "inline-block",
              }} />
            ))}
          </div>
          <span style={{ fontSize: 9, color: T.textMuted, fontFamily: "'Share Tech Mono', monospace", letterSpacing: 1 }}>PROCESSING</span>
        </div>
      )}
      {(thoughts || []).slice().reverse().map((t, i) => {
        const isPos = t.level === "success" || t.message?.includes("BUY") || t.message?.includes("bullish");
        const isNeg = t.level === "error" || t.message?.includes("SELL") || t.message?.includes("rejected");
        const dotColor = isNeg ? T.red : isPos ? T.green : T.accent;
        return (
          <div key={i} style={{
            display: "flex", gap: 8, alignItems: "flex-start",
            padding: "5px 0", borderBottom: `1px solid ${T.border}15`,
            animation: "fadeSlideIn 0.3s ease",
          }}>
            <span style={{ fontSize: 8.5, color: T.textDim, minWidth: 42, fontFamily: "'Share Tech Mono', monospace", paddingTop: 1 }}>
              {t.timestamp ? new Date(t.timestamp).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }) : "—"}
            </span>
            <span style={{ width: 5, height: 5, borderRadius: "50%", background: dotColor, marginTop: 4, flexShrink: 0, boxShadow: `0 0 4px ${dotColor}` }} />
            <span style={{ fontSize: 10, color: isNeg ? T.red : isPos ? T.green : T.textSub, flex: 1, lineHeight: 1.6 }}
              dangerouslySetInnerHTML={{ __html: t.message || "" }} />
          </div>
        );
      })}
      {!thoughts?.length && (
        <div style={{ fontSize: 11, color: T.textMuted, padding: "12px 0", fontFamily: "'Share Tech Mono', monospace" }}>
          Waiting for replay data…
        </div>
      )}
    </div>
  );
}

// ─── STAGE PIPELINE ──────────────────────────────────────────────────────────
function StagePipeline({ currentStage, T }) {
  const stages = [
    { id: "collecting_context", label: "context" },
    { id: "calling_model", label: "AI call" },
    { id: "risk_checks", label: "risk" },
    { id: "placing_orders", label: "execute" },
    { id: "decision_complete", label: "done" },
  ];
  const currentIdx = stages.findIndex(s => s.id === currentStage);

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 0, overflowX: "auto", marginBottom: 10, paddingBottom: 2 }}>
      {stages.map((s, i) => {
        const isDone = currentIdx > i;
        const isActive = currentIdx === i;
        const col = isActive ? T.accent : isDone ? T.green : T.textMuted;
        return (
          <div key={s.id} style={{ display: "flex", alignItems: "center", flexShrink: 0 }}>
            <div style={{
              padding: "4px 10px", fontSize: 8, letterSpacing: 1, textTransform: "uppercase",
              border: `1px solid ${col}${isActive ? "ff" : isDone ? "80" : "30"}`,
              color: col, background: isActive ? `${T.accent}08` : isDone ? `${T.green}04` : "transparent",
              fontFamily: "'Share Tech Mono', monospace", whiteSpace: "nowrap",
              boxShadow: isActive ? `0 0 8px ${T.accent}20` : "none",
              transition: "all 0.3s",
            }}>
              {isActive && "▶ "}{s.label}
            </div>
            {i < stages.length - 1 && (
              <div style={{ width: 14, height: 1, background: T.border, position: "relative" }}>
                <span style={{ position: "absolute", right: -4, top: -5, fontSize: 6, color: T.textMuted }}>▶</span>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ─── SIGNAL CARD ─────────────────────────────────────────────────────────────
function SignalCard({ signal, T }) {
  const conf = Math.round((signal.confidence || 0) * 100);
  const confColor = conf >= 70 ? T.green : conf >= 50 ? T.amber : T.red;
  const actionColor = ACTION_COLORS_FN(T)[signal.action] || T.textMuted;
  const stratColor = STRATEGY_COLORS_FN(T)[signal.strategy] || T.textMuted;

  return (
    <div style={{
      border: `1px solid ${T.border}`, padding: "12px 14px", marginBottom: 8,
      position: "relative", overflow: "hidden", transition: "all 0.15s",
      borderLeft: `2px solid ${actionColor}`,
      background: `linear-gradient(135deg, ${actionColor}04, transparent)`,
    }}>
      <div style={{ display: "flex", gap: 8, marginBottom: 8, alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ fontWeight: 700, fontSize: 13, color: T.text, fontFamily: "'Share Tech Mono', monospace" }}>{signal.symbol}</span>
        <Pill label={signal.action} color={actionColor} T={T} />
        <span style={{ marginLeft: "auto" }}><Pill label={signal.strategy || "—"} color={stratColor} T={T} /></span>
      </div>
      <div style={{ display: "flex", gap: 10, marginBottom: 8, flexWrap: "wrap" }}>
        {[
          { l: "Entry", v: signal.entry_price ? `₹${Number(signal.entry_price).toFixed(0)}` : "—" },
          { l: "SL", v: signal.stop_loss ? `₹${Number(signal.stop_loss).toFixed(0)}` : "—", c: T.red },
          { l: "Target", v: signal.target ? `₹${Number(signal.target).toFixed(0)}` : "—", c: T.green },
          { l: "R/R", v: signal.risk_reward ? Number(signal.risk_reward).toFixed(1) : "—" },
        ].map(it => (
          <div key={it.l} style={{ fontSize: 9.5, color: T.textMuted, fontFamily: "'Share Tech Mono', monospace" }}>
            {it.l} <span style={{ color: it.c || T.text }}>{it.v}</span>
          </div>
        ))}
      </div>
      <div style={{ marginBottom: 6 }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
          <span style={{ fontSize: 8, color: T.textMuted, fontFamily: "'Share Tech Mono', monospace", letterSpacing: 1 }}>CONFIDENCE</span>
          <Mono size={9} color={confColor}>{conf}%</Mono>
        </div>
        <ProgressBar value={conf} max={100} color={confColor} height={3} />
      </div>
      {signal.rationale && (
        <p style={{ fontSize: 10, color: T.textSub, lineHeight: 1.6, marginTop: 6, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
          {signal.rationale}
        </p>
      )}
    </div>
  );
}

// ─── LIVE TRADE ROW ──────────────────────────────────────────────────────────
function TradeRow({ trade, T }) {
  const actionColor = trade.action === "BUY" ? T.green : T.red;
  const pnl = trade.pnl;
  const pnlColor = pnl === null || pnl === undefined ? T.textMuted : pnl >= 0 ? T.green : T.red;

  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8, padding: "6px 0",
      borderBottom: `1px solid ${T.border}15`, fontSize: 9.5,
      animation: "fadeSlideIn 0.25s ease",
    }}>
      <span style={{ color: T.textDim, minWidth: 36, fontFamily: "'Share Tech Mono', monospace" }}>{trade.time || "—"}</span>
      <span style={{ fontWeight: 700, minWidth: 60, color: T.text, fontFamily: "'Share Tech Mono', monospace" }}>{trade.symbol}</span>
      <span style={{
        fontSize: 8, letterSpacing: 1, fontWeight: 700, padding: "1px 5px",
        background: `${actionColor}15`, color: actionColor, border: `1px solid ${actionColor}30`,
      }}>{trade.action}</span>
      <span style={{ color: T.textSub }}>₹{Number(trade.price || 0).toFixed(0)}</span>
      <span style={{ color: T.textMuted }}>×{trade.quantity || trade.qty}</span>
      {pnl !== null && pnl !== undefined && (
        <span style={{ marginLeft: "auto", fontWeight: 700, color: pnlColor }}>
          {pnl >= 0 ? "+" : ""}₹{Number(pnl).toFixed(0)}
        </span>
      )}
    </div>
  );
}

// ─── POSITIONS PANEL ─────────────────────────────────────────────────────────
function PositionsPanel({ positions, tickHistory, T }) {
  const { isMobile } = useBreakpoint();
  if (!positions?.length) return (
    <Card T={T} accent={T.green}>
      <CardHeader T={T} title="Open Positions" subtitle="Live P&L with sparklines" accent={T.green} />
      <div style={{ padding: "48px 16px", textAlign: "center", color: T.textMuted, fontSize: 12, fontFamily: "'Share Tech Mono', monospace", letterSpacing: 2 }}>NO OPEN POSITIONS</div>
    </Card>
  );
  return (
    <Card T={T} accent={T.green}>
      <CardHeader T={T} title="Open Positions" subtitle="Live P&L with sparklines" accent={T.green}
        right={<Pill label={`${positions.length} open`} color={T.accent} T={T} />}
      />
      <div>
        {positions.map((p, i) => {
          const ltp = Number(p.ltp || p.avg || 0);
          const avg = Number(p.avg || 0);
          const qty = Number(p.qty || 0);
          const pnl = Number(p.pnl || 0);
          const pnlPct = avg > 0 ? ((ltp - avg) / avg * 100) : 0;
          const series = tickHistory?.[p.symbol] || [];
          const isUp = pnl >= 0;
          const slDist = p.stop_loss ? Math.abs(((ltp - Number(p.stop_loss)) / ltp) * 100) : null;
          return (
            <div key={i} style={{ borderBottom: i < positions.length - 1 ? `1px solid ${T.border}` : "none", padding: "14px 16px", position: "relative" }}>
              <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: 2, background: isUp ? T.green : T.red, boxShadow: `0 0 8px ${isUp ? T.green : T.red}` }} />
              {isMobile ? (
                <>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <span style={{ fontWeight: 700, fontSize: 13, color: T.text, fontFamily: "'Share Tech Mono', monospace" }}>{p.symbol}</span>
                      <Pill label={p.side} color={p.side === "BUY" ? T.green : T.red} T={T} />
                    </div>
                    <Delta value={pnl} size={13} />
                  </div>
                  <Sparkline data={series.length ? series : [avg, ltp]} color={isUp ? T.green : T.red} height={28} />
                </>
              ) : (
                <div style={{ display: "grid", gridTemplateColumns: "120px 55px 100px 110px 110px 80px 1fr", alignItems: "center", gap: 14 }}>
                  <div>
                    <div style={{ fontWeight: 700, fontSize: 12, color: T.text, marginBottom: 4, fontFamily: "'Share Tech Mono', monospace" }}>{p.symbol}</div>
                    <Pill label={p.side} color={p.side === "BUY" ? T.green : T.red} T={T} />
                  </div>
                  <Mono size={11} color={T.textSub}>{qty}</Mono>
                  <div>
                    <div style={{ fontSize: 9, color: T.textMuted, marginBottom: 2, fontFamily: "'Share Tech Mono', monospace", letterSpacing: 1 }}>AVG / LTP</div>
                    <Mono size={10} color={T.text}>₹{avg.toFixed(0)} / <span style={{ color: isUp ? T.green : T.red }}>₹{ltp.toFixed(0)}</span></Mono>
                  </div>
                  <div>
                    <div style={{ marginBottom: 2 }}><Delta value={pnl} size={13} /></div>
                    <Delta value={pnlPct} size={10} />%
                  </div>
                  <div>
                    {slDist !== null ? (
                      <>
                        <div style={{ fontSize: 9, color: T.textMuted, marginBottom: 4, fontFamily: "'Share Tech Mono', monospace", letterSpacing: 1 }}>SL DIST</div>
                        <Mono size={11} color={slDist < 0.5 ? T.red : slDist < 1.5 ? T.amber : T.green}>{slDist.toFixed(2)}%</Mono>
                        <div style={{ marginTop: 4 }}><ProgressBar value={Math.min(slDist, 5)} max={5} color={slDist < 1 ? T.red : T.amber} /></div>
                      </>
                    ) : <span style={{ fontSize: 10, color: T.textMuted }}>No SL</span>}
                  </div>
                  <div>
                    {p.target ? (
                      <>
                        <div style={{ fontSize: 9, color: T.textMuted, marginBottom: 2, fontFamily: "'Share Tech Mono', monospace", letterSpacing: 1 }}>TARGET</div>
                        <Mono size={11} color={T.accent}>₹{Number(p.target).toFixed(0)}</Mono>
                      </>
                    ) : <Mono size={10} color={T.textMuted}>—</Mono>}
                  </div>
                  <Sparkline data={series.length ? series : [avg, ltp]} color={isUp ? T.green : T.red} height={36} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </Card>
  );
}

// ─── SL TRACKER ──────────────────────────────────────────────────────────────
function SLTrackerPanel({ positions, ticks, T }) {
  const { isMobile } = useBreakpoint();
  const items = useMemo(() => (positions || []).map(p => {
    const ltp = tickPrice(ticks?.[p.symbol]) || Number(p.ltp || 0);
    const sl = Number(p.stop_loss || p.current_sl || 0);
    const entry = Number(p.avg || p.entry_price || 0);
    const target = Number(p.target || 0);
    const slDist = sl && ltp ? Math.abs(((ltp - sl) / ltp) * 100) : null;
    const targetDist = target && ltp ? Math.abs(((target - ltp) / ltp) * 100) : null;
    const progress = (entry && target && ltp && entry !== target)
      ? Math.max(0, Math.min(100, ((ltp - entry) / (target - entry)) * 100)) : 0;
    return { ...p, ltp, sl, entry, target, slDist, targetDist, progress };
  }), [positions, ticks]);

  return (
    <Card T={T} accent={T.amber}>
      <CardHeader T={T} title="SL / Target Tracker" subtitle="Stop-loss status · trailing stops · target proximity" accent={T.amber} />
      <div style={{ padding: "12px 16px" }}>
        {items.map((p, i) => (
          <div key={i} style={{ marginBottom: 12, padding: "12px 14px", background: T.bg, borderRadius: 3, border: `1px solid ${T.border}`, borderLeft: `2px solid ${p.progress > 50 ? T.green : T.amber}` }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <span style={{ fontWeight: 700, fontSize: 12, color: T.text, fontFamily: "'Share Tech Mono', monospace" }}>{p.symbol}</span>
                <Pill label={p.side || "BUY"} color={(p.side || "") === "SELL" ? T.red : T.green} T={T} />
              </div>
              <Mono size={14} color={T.text} weight={700}>₹{p.ltp.toFixed(0)}</Mono>
            </div>
            <div style={{ position: "relative", height: 6, background: T.border, borderRadius: 0, margin: "0 0 12px", overflow: "visible" }}>
              <div style={{ position: "absolute", left: 0, width: `${p.progress}%`, height: "100%", background: `linear-gradient(90deg, ${T.accent}60, ${T.accent})`, boxShadow: `0 0 8px ${T.accent}60` }} />
              <div style={{ position: "absolute", left: `${p.progress}%`, top: -5, transform: "translateX(-50%)", width: 14, height: 14, background: T.accent, borderRadius: "50%", border: `2px solid ${T.card}`, boxShadow: `0 0 0 2px ${T.accent}60, 0 0 8px ${T.accent}` }} />
            </div>
            <div style={{ display: "grid", gridTemplateColumns: isMobile ? "repeat(2,1fr)" : "repeat(4,1fr)", gap: 8 }}>
              {[
                { label: "Entry", v: `₹${p.entry.toFixed(0)}`, c: T.textSub },
                { label: "Stop Loss", v: p.sl ? `₹${p.sl.toFixed(0)}` : "—", c: T.red, sub: p.slDist ? `${p.slDist.toFixed(1)}% away` : null },
                { label: "Target", v: p.target ? `₹${p.target.toFixed(0)}` : "—", c: T.green, sub: p.targetDist ? `${p.targetDist.toFixed(1)}% away` : null },
                { label: "Progress", v: `${p.progress.toFixed(0)}%`, c: p.progress > 50 ? T.green : T.amber },
              ].map(it => (
                <div key={it.label}>
                  <div style={{ fontSize: 8.5, color: T.textMuted, marginBottom: 3, fontFamily: "'Share Tech Mono', monospace", letterSpacing: 1 }}>{it.label}</div>
                  <Mono size={11} color={it.c} weight={700}>{it.v}</Mono>
                  {it.sub && <div style={{ fontSize: 9, color: T.textMuted, marginTop: 1 }}>{it.sub}</div>}
                </div>
              ))}
            </div>
          </div>
        ))}
        {!items.length && <div style={{ fontSize: 12, color: T.textMuted, padding: "24px 0", fontFamily: "'Share Tech Mono', monospace", letterSpacing: 1 }}>NO TRACKED POSITIONS</div>}
      </div>
    </Card>
  );
}

// ─── AI TIMELINE ─────────────────────────────────────────────────────────────
function AITimelinePanel({ decisions, T }) {
  const { isMobile } = useBreakpoint();
  const chartData = useMemo(() => (decisions || []).slice(-30).map((d, i, arr) => ({
    time: d.timestamp ? new Date(d.timestamp).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }) : `T-${arr.length - i}`,
    confidence: Math.round(((d.signals || [])[0]?.confidence || 0.5) * 100),
    latency: d.latency_ms || 0,
  })), [decisions]);

  if (!chartData.length) return (
    <Card T={T} accent={T.purple}>
      <CardHeader T={T} title="AI Confidence Timeline" subtitle="Signal quality over time" accent={T.purple} />
      <div style={{ padding: "48px 16px", textAlign: "center", color: T.textMuted, fontSize: 12, fontFamily: "'Share Tech Mono', monospace", letterSpacing: 2 }}>NO DECISION HISTORY YET</div>
    </Card>
  );

  return (
    <Card T={T} accent={T.purple}>
      <CardHeader T={T} title="AI Confidence Timeline" subtitle="Signal confidence & latency per cycle" accent={T.purple} />
      <div style={{ padding: "14px 16px" }}>
        <ResponsiveContainer width="100%" height={isMobile ? 150 : 180}>
          <ComposedChart data={chartData} margin={{ left: isMobile ? -10 : 0, right: isMobile ? -10 : 0 }}>
            <CartesianGrid strokeDasharray="2 2" stroke={T.border} />
            <XAxis dataKey="time" tick={{ fill: T.textMuted, fontSize: 9, fontFamily: "'Share Tech Mono', monospace" }} tickLine={false} axisLine={false} interval={isMobile ? 6 : 4} />
            <YAxis yAxisId="c" domain={[0, 100]} tick={{ fill: T.textMuted, fontSize: 9, fontFamily: "'Share Tech Mono', monospace" }} tickLine={false} axisLine={false} tickFormatter={v => `${v}%`} width={38} />
            <YAxis yAxisId="l" orientation="right" tick={{ fill: T.textMuted, fontSize: 9, fontFamily: "'Share Tech Mono', monospace" }} tickLine={false} axisLine={false} tickFormatter={v => `${v}ms`} width={42} />
            <Tooltip {...tooltipStyle(T)} formatter={(v, n) => [n === "confidence" ? `${v}%` : `${v}ms`, n]} />
            <ReferenceLine yAxisId="c" y={65} stroke={T.amber} strokeDasharray="4 2" strokeWidth={1} opacity={0.6} />
            <Bar yAxisId="c" dataKey="confidence" fill={T.accent} opacity={0.12} radius={[2, 2, 0, 0]} />
            <Line yAxisId="c" type="monotone" dataKey="confidence" stroke={T.accent} strokeWidth={2} dot={false} style={{ filter: `drop-shadow(0 0 4px ${T.accent})` }} />
            <Line yAxisId="l" type="monotone" dataKey="latency" stroke={T.purple} strokeWidth={1.5} dot={false} strokeDasharray="4 2" />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}

// ─── STRATEGY REVIEW ─────────────────────────────────────────────────────────
function StrategyReviewPanel({ reviewData, T }) {
  const { isMobile } = useBreakpoint();
  const STRAT_C = STRATEGY_COLORS_FN(T);
  const review = reviewData || {
    strategy_weights: { momentum: 0.30, mean_reversion: 0.25, options_selling: 0.30, breakout: 0.15 },
    avoid_patterns: ["Avoid chasing breakouts in first 30 minutes", "Skip signals when VIX > 22"],
    focus_patterns: ["High-volume MACD crossovers between 10:30–2:00", "Mean reversion at BB extremes with RSI confirmation"],
    overall_assessment: "Momentum and options selling strategies are performing best. Mean reversion needs tighter stops.",
    parameter_adjustments: { rsi_overbought: 72, confidence_threshold: 0.70 },
  };
  const weights = review.strategy_weights || {};
  const maxW = Math.max(...Object.values(weights));
  return (
    <Card T={T} accent={T.purple}>
      <CardHeader T={T} title="AI Strategy Review" subtitle="Performance review & parameter adjustments" accent={T.purple}
        right={<Pill label="auto-updated" color={T.green} T={T} />}
      />
      <div style={{ padding: "14px 16px", display: "grid", gridTemplateColumns: isMobile ? "1fr" : "1fr 1fr", gap: 16 }}>
        <div>
          <div style={{ fontSize: 8.5, color: T.textMuted, letterSpacing: 1.5, marginBottom: 12, textTransform: "uppercase", fontWeight: 700, fontFamily: "'Share Tech Mono', monospace" }}>Strategy Weights</div>
          {Object.entries(weights).map(([k, v]) => (
            <div key={k} style={{ marginBottom: 14 }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                <span style={{ fontSize: 11, color: T.text, fontFamily: "'Share Tech Mono', monospace" }}>{k.replace(/_/g, " ")}</span>
                <Mono size={11} color={STRAT_C[k] || T.accent}>{(v * 100).toFixed(0)}%</Mono>
              </div>
              <ProgressBar value={v} max={maxW} color={STRAT_C[k] || T.accent} height={4} />
            </div>
          ))}
          <div style={{ marginTop: 14, padding: "10px 12px", background: T.bg, borderRadius: 3, border: `1px solid ${T.border}` }}>
            <div style={{ fontSize: 8.5, color: T.textMuted, letterSpacing: 1.5, marginBottom: 8, textTransform: "uppercase", fontFamily: "'Share Tech Mono', monospace" }}>Parameter Adjustments</div>
            {Object.entries(review.parameter_adjustments || {}).map(([k, v]) => (
              <div key={k} style={{ display: "flex", justifyContent: "space-between", marginBottom: 5 }}>
                <span style={{ fontSize: 10, color: T.textMuted, fontFamily: "'Share Tech Mono', monospace" }}>{k.replace(/_/g, " ")}</span>
                <Mono size={10.5} color={T.accent}>{v}</Mono>
              </div>
            ))}
          </div>
        </div>
        <div>
          <div style={{ fontSize: 8.5, color: T.textMuted, letterSpacing: 1.5, marginBottom: 8, textTransform: "uppercase", fontFamily: "'Share Tech Mono', monospace" }}>Assessment</div>
          <p style={{ fontSize: 11, color: T.textSub, lineHeight: 1.8, padding: "10px 12px", background: T.bg, borderRadius: 3, marginBottom: 12, borderLeft: `2px solid ${T.accent}40` }}>{review.overall_assessment}</p>
          <div style={{ marginBottom: 10 }}>
            <div style={{ fontSize: 8.5, color: T.green, letterSpacing: 1.5, textTransform: "uppercase", marginBottom: 6, display: "flex", gap: 5, alignItems: "center", fontFamily: "'Share Tech Mono', monospace" }}>
              <CheckCircle size={9} /> Focus Patterns
            </div>
            {(review.focus_patterns || []).map((p, i) => (
              <div key={i} style={{ fontSize: 10, color: T.textSub, padding: "6px 10px", background: T.greenDim, borderLeft: `2px solid ${T.green}60`, borderRadius: 2, marginBottom: 4 }}>{p}</div>
            ))}
          </div>
          <div>
            <div style={{ fontSize: 8.5, color: T.red, letterSpacing: 1.5, textTransform: "uppercase", marginBottom: 6, display: "flex", gap: 5, alignItems: "center", fontFamily: "'Share Tech Mono', monospace" }}>
              <XCircle size={9} /> Avoid Patterns
            </div>
            {(review.avoid_patterns || []).map((p, i) => (
              <div key={i} style={{ fontSize: 10, color: T.textSub, padding: "6px 10px", background: T.redDim, borderLeft: `2px solid ${T.red}60`, borderRadius: 2, marginBottom: 4 }}>{p}</div>
            ))}
          </div>
        </div>
      </div>
    </Card>
  );
}

// ─── MODEL PERFORMANCE ───────────────────────────────────────────────────────
function ModelPerformancePanel({ decisions, T }) {
  const { isMobile } = useBreakpoint();
  const stats = useMemo(() => {
    const all = decisions || [];
    const counts = {}, lats = {};
    all.forEach(d => {
      const m = d.model_used || "unknown";
      counts[m] = (counts[m] || 0) + 1;
      if (!lats[m]) lats[m] = [];
      if (d.latency_ms) lats[m].push(d.latency_ms);
    });
    return Object.entries(counts).map(([model, count]) => ({
      model: model.replace("gemini-", "").replace("-preview", "").replace("-latest", ""),
      count, pct: all.length ? Math.round((count / all.length) * 100) : 0,
      avgLatency: lats[model]?.length ? Math.round(lats[model].reduce((a, b) => a + b, 0) / lats[model].length) : 0,
      isFallback: model !== (all[0]?.model_requested || model),
    })).sort((a, b) => b.count - a.count);
  }, [decisions]);

  const display = stats.length ? stats : [
    { model: "2.5-flash", pct: 82, isFallback: false, avgLatency: 1240 },
    { model: "2.0-flash", pct: 12, isFallback: true, avgLatency: 890 },
  ];
  const fallbackRate = display.filter(m => m.isFallback).reduce((s, x) => s + x.pct, 0);
  return (
    <Card T={T} accent={T.blue}>
      <CardHeader T={T} title="Model Performance" subtitle="Gemini usage & fallback tracking" accent={T.blue}
        right={<Pill label={fallbackRate > 0 ? `${fallbackRate}% fallback` : "100% primary"} color={fallbackRate > 0 ? T.amber : T.green} T={T} />}
      />
      <div style={{ padding: "14px 16px", display: "grid", gridTemplateColumns: isMobile ? "1fr" : "repeat(2,1fr)", gap: 10 }}>
        {display.map(m => (
          <div key={m.model} style={{ background: T.bg, borderRadius: 3, padding: "12px 14px", border: `1px solid ${(m.isFallback ? T.amber : T.green) + "25"}`, borderTop: `2px solid ${m.isFallback ? T.amber : T.green}` }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8, alignItems: "center" }}>
              <Mono size={10.5} color={T.text} weight={700}>gemini-{m.model}</Mono>
              <Pill label={m.isFallback ? "fallback" : "primary"} color={m.isFallback ? T.amber : T.green} T={T} />
            </div>
            <ProgressBar value={m.pct} max={100} color={m.isFallback ? T.amber : T.accent} height={3} />
            <div style={{ display: "flex", justifyContent: "space-between", marginTop: 7 }}>
              <span style={{ fontSize: 9.5, color: T.textMuted, fontFamily: "'Share Tech Mono', monospace" }}>{m.pct}% calls</span>
              <span style={{ fontSize: 9.5, color: T.textMuted, fontFamily: "'Share Tech Mono', monospace" }}>{m.avgLatency}ms avg</span>
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

// ─── KILL SWITCH ─────────────────────────────────────────────────────────────
function KillSwitchPanel({ risk, riskEvents, T, onReset }) {
  const daily_pnl_pct = risk?.daily_pnl_pct || 0;
  const drawdown = risk?.drawdown_pct || 0;
  const killSwitch = risk?.kill_switch;
  const events = (riskEvents?.events || []).filter(e => e.type?.includes("KILL") || e.type?.includes("STOP") || e.severity === "CRITICAL");
  return (
    <Card T={T} accent={killSwitch ? T.red : T.green}>
      <CardHeader T={T} title="Kill Switch Monitor" subtitle="Safety gauges & trigger history" accent={killSwitch ? T.red : T.green}
        right={
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <Pill label={killSwitch ? "TRIGGERED" : "SAFE"} color={killSwitch ? T.red : T.green} T={T} />
            {killSwitch && (
              <button onClick={onReset} style={{ background: T.redDim, border: `1px solid ${T.red}50`, borderRadius: 3, padding: "4px 10px", cursor: "pointer", fontSize: 9, color: T.red, fontWeight: 700, fontFamily: "'Share Tech Mono', monospace", letterSpacing: 1 }}>RESET</button>
            )}
          </div>
        }
      />
      <div style={{ padding: "14px 16px" }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 10, marginBottom: 14 }}>
          {[
            { label: "Daily Loss", value: Math.abs(Math.min(0, daily_pnl_pct)), max: 2.0, color: T.red },
            { label: "Drawdown", value: drawdown, max: 8.0, color: T.amber },
          ].map(g => {
            const pct = Math.min((g.value / g.max) * 100, 100);
            return (
              <div key={g.label} style={{ background: T.bg, borderRadius: 3, padding: "12px 14px", border: `1px solid ${T.border}` }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                  <span style={{ fontSize: 9.5, color: T.textMuted, fontFamily: "'Share Tech Mono', monospace", letterSpacing: 1 }}>{g.label}</span>
                  <Mono size={11} color={pct > 75 ? g.color : T.text}>{g.value.toFixed(2)}% / {g.max}%</Mono>
                </div>
                <div style={{ position: "relative", height: 8, background: T.border, borderRadius: 0 }}>
                  <div style={{ position: "absolute", width: `${pct}%`, height: "100%", background: `linear-gradient(90deg, ${g.color}70, ${g.color})`, boxShadow: pct > 75 ? `0 0 10px ${g.color}80` : "none" }} />
                  <div style={{ position: "absolute", left: "75%", top: 0, height: "100%", width: 1, background: T.textDim }} />
                </div>
              </div>
            );
          })}
        </div>
        <div style={{ fontSize: 8.5, color: T.textMuted, letterSpacing: 1.5, textTransform: "uppercase", marginBottom: 8, fontFamily: "'Share Tech Mono', monospace" }}>Recent Risk Events</div>
        {events.length === 0 ? (
          <div style={{ fontSize: 11, color: T.textMuted, padding: "12px 0", fontFamily: "'Share Tech Mono', monospace" }}>No critical events — system nominal</div>
        ) : events.slice(0, 5).map((e, i) => (
          <div key={i} style={{ display: "flex", gap: 8, alignItems: "flex-start", padding: "8px 10px", background: T.bg, borderRadius: 2, marginBottom: 4, borderLeft: `2px solid ${T.red}` }}>
            <AlertTriangle size={11} color={T.red} style={{ marginTop: 1, flexShrink: 0 }} />
            <Mono size={9.5} color={T.textMuted}>{e.timestamp ? new Date(e.timestamp).toLocaleTimeString("en-IN") : "—"}</Mono>
            <span style={{ fontSize: 11, color: T.textSub, flex: 1 }}>{e.description || e.message}</span>
            <Pill label={e.severity || "CRITICAL"} color={T.red} T={T} />
          </div>
        ))}
      </div>
    </Card>
  );
}

// ─── OPTIONS CHAIN ───────────────────────────────────────────────────────────
function OptionsChainPanel({ data, T }) {
  const opts = data || {};
  const nifty = opts.NIFTY || {};
  const bnk = opts.BANKNIFTY || {};
  const pcr = Number(nifty.pcr);
  const hasPcr = Number.isFinite(pcr);
  const pcrColor = pcr > 1.2 ? T.green : pcr < 0.8 ? T.red : T.amber;
  const pcrLabel = pcr > 1.5 ? "EXTREME BULL" : pcr > 1.2 ? "BULLISH" : pcr > 0.8 ? "NEUTRAL" : pcr > 0.5 ? "BEARISH" : "EXTREME BEAR";
  const callOI = (nifty.top_call_oi || []).map((s, i) => ({ strike: s, oi: [4.2, 3.1, 2.8][i] || 1.5, type: "CE" }));
  const putOI = (nifty.top_put_oi || []).map((s, i) => ({ strike: s, oi: [3.8, 2.9, 2.1][i] || 1.2, type: "PE" }));
  const combined = [...putOI.reverse(), ...callOI].map(x => ({ ...x, label: `${x.strike}${x.type}` }));
  const { isMobile } = useBreakpoint();
  return (
    <Card T={T} accent={T.accent}>
      <CardHeader T={T} title="Options Chain" subtitle="PCR · Max Pain · OI Heatmap" accent={T.accent}
        right={
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span style={{ fontSize: 9, color: T.textMuted, fontFamily: "'Share Tech Mono', monospace", letterSpacing: 1 }}>PCR</span>
            <Mono size={16} color={hasPcr ? pcrColor : T.textMuted} weight={700}>{hasPcr ? pcr.toFixed(2) : "—"}</Mono>
            {hasPcr && <Pill label={pcrLabel} color={pcrColor} T={T} />}
          </div>
        }
      />
      <div style={{ padding: "14px 16px", display: "grid", gridTemplateColumns: isMobile ? "1fr" : "1.5fr 1fr", gap: 16 }}>
        <div>
          <div style={{ fontSize: 8.5, fontWeight: 700, letterSpacing: 1.5, color: T.textMuted, textTransform: "uppercase", marginBottom: 10, fontFamily: "'Share Tech Mono', monospace" }}>OI Concentration — NIFTY</div>
          {combined.length === 0 ? (
            <div style={{ height: 150, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11, color: T.textMuted, background: T.bg, borderRadius: 3, border: `1px dashed ${T.border}` }}>No live OI data</div>
          ) : (
            <ResponsiveContainer width="100%" height={150}>
              <BarChart data={combined} layout="vertical" margin={{ left: 55, right: 8 }}>
                <XAxis type="number" tick={{ fill: T.textMuted, fontSize: 9, fontFamily: "'Share Tech Mono', monospace" }} tickLine={false} axisLine={false} />
                <YAxis type="category" dataKey="label" tick={{ fill: T.textMuted, fontSize: 9, fontFamily: "'Share Tech Mono', monospace" }} tickLine={false} axisLine={false} width={50} />
                <Tooltip {...tooltipStyle(T)} formatter={(v, n, p) => [`${v.toFixed(1)}L`, p.payload.type === "CE" ? "Call OI" : "Put OI"]} />
                <Bar dataKey="oi" radius={[0, 2, 2, 0]}>
                  {combined.map((e, i) => <Cell key={i} fill={e.type === "CE" ? T.red : T.green} opacity={0.85} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 4 }}>
          {[
            { label: "ATM Strike", v: nifty.atm_strike?.toLocaleString("en-IN") || "—", c: T.cyan },
            { label: "Straddle Price", v: Number.isFinite(Number(nifty.atm_straddle)) ? `₹${nifty.atm_straddle}` : "—", c: T.purple },
            { label: "Expected Move", v: Number.isFinite(Number(nifty.expected_move_pct)) ? `±${Number(nifty.expected_move_pct).toFixed(2)}%` : "—", c: T.amber },
            { label: "Max Pain", v: nifty.max_pain?.toLocaleString("en-IN") || "—", c: T.amber },
            { label: "Key Resistance", v: nifty.key_resistance?.toLocaleString("en-IN") || "—", c: T.red },
            { label: "Key Support", v: nifty.key_support?.toLocaleString("en-IN") || "—", c: T.green },
            { label: "BANKNIFTY PCR", v: Number.isFinite(Number(bnk.pcr)) ? Number(bnk.pcr).toFixed(2) : "—", c: Number(bnk.pcr) > 1 ? T.green : T.red },
          ].map(r => (
            <div key={r.label} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 10px", background: T.bg, borderRadius: 2, borderLeft: `2px solid ${r.c}30` }}>
              <span style={{ fontSize: 9.5, color: T.textMuted, fontFamily: "'Share Tech Mono', monospace", letterSpacing: 0.5 }}>{r.label}</span>
              <Mono size={10.5} color={r.c} weight={700}>{r.v}</Mono>
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}

// ─── WATCHLIST ───────────────────────────────────────────────────────────────
const SIGNAL_LABEL = { strong_buy: "STRONG BUY", buy: "BUY", neutral: "NEUTRAL", sell: "SELL", strong_sell: "STRONG SELL" };
function WatchlistPanel({ data, T }) {
  const items = data?.length ? data : [];
  const [selected, setSelected] = useState(null);
  const { isMobile } = useBreakpoint();
  const SC = SIGNAL_COLORS_FN(T);
  return (
    <Card T={T} accent={T.blue}>
      <CardHeader T={T} title="Watchlist" subtitle="Indicator matrix · live signals" accent={T.blue}
        right={<Pill label={`${items.length} symbols`} color={T.textMuted} T={T} />}
      />
      <div style={{ padding: "0 16px 12px", overflowX: "auto" }}>
        {isMobile ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 5, marginTop: 10 }}>
            {items.map(w => {
              const ind = w.indicators || {};
              const sig = ind.overall_signal || "neutral";
              const rsi_val = Number(ind.rsi || 50);
              const isSel = selected === w.symbol;
              return (
                <div key={w.symbol}>
                  <div onClick={() => setSelected(isSel ? null : w.symbol)} style={{ background: isSel ? `${T.accent}08` : T.bg, border: `1px solid ${isSel ? T.accent + "50" : T.border}`, borderRadius: 3, padding: "10px 12px", cursor: "pointer" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                        <span style={{ fontWeight: 700, fontSize: 12, color: T.text, fontFamily: "'Share Tech Mono', monospace" }}>{w.symbol}</span>
                        <Mono size={11} color={T.text}>₹{(w.ltp || 0).toFixed(0)}</Mono>
                        <Delta value={w.change_pct || 0} size={10} />
                      </div>
                      <Pill label={SIGNAL_LABEL[sig] || sig} color={SC[sig] || T.textMuted} T={T} />
                    </div>
                  </div>
                </div>
              );
            })}
            {!items.length && <div style={{ fontSize: 11, color: T.textMuted, padding: "20px 0", fontFamily: "'Share Tech Mono', monospace" }}>NO WATCHLIST DATA</div>}
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", marginTop: 10, minWidth: 560 }}>
            <thead>
              <tr style={{ borderBottom: `1px solid ${T.border}` }}>
                {["Symbol", "LTP", "Chg%", "RSI", "MACD", "BB", "Volume", "Signal"].map(h =>
                  <th key={h} style={{ padding: "7px 8px", textAlign: "left", fontSize: 8.5, color: T.textMuted, fontWeight: 700, letterSpacing: 1.5, textTransform: "uppercase", fontFamily: "'Share Tech Mono', monospace" }}>{h}</th>
                )}
              </tr>
            </thead>
            <tbody>
              {items.map((w, i) => {
                const ind = w.indicators || {};
                const sig = ind.overall_signal || "neutral";
                const rsi_val = Number(ind.rsi || 50);
                return (
                  <tr key={w.symbol} style={{ borderBottom: `1px solid ${T.border}15`, background: i % 2 ? T.bg + "60" : "transparent" }}>
                    <td style={{ padding: "9px 8px", fontWeight: 700, fontSize: 11, color: T.text, fontFamily: "'Share Tech Mono', monospace" }}>{w.symbol}</td>
                    <td style={{ padding: "9px 8px" }}><Mono size={11} color={T.text}>₹{(w.ltp || 0).toFixed(0)}</Mono></td>
                    <td style={{ padding: "9px 8px" }}><Delta value={w.change_pct || 0} size={10} /></td>
                    <td style={{ padding: "9px 8px" }}><Mono size={10.5} color={rsi_val > 70 ? T.red : rsi_val < 30 ? T.green : T.text}>{rsi_val}</Mono></td>
                    <td style={{ padding: "9px 8px" }}><Pill label={ind.macd_signal || "—"} color={ind.macd_signal === "bullish" ? T.green : ind.macd_signal === "bearish" ? T.red : T.amber} T={T} /></td>
                    <td style={{ padding: "9px 8px" }}><Pill label={ind.bb_signal || "mid"} color={ind.bb_signal === "upper" ? T.amber : ind.bb_signal === "lower" ? T.blue : T.textMuted} T={T} /></td>
                    <td style={{ padding: "9px 8px" }}><Mono size={10.5} color={(ind.volume_ratio || 1) > 1.5 ? T.amber : T.textMuted}>{(ind.volume_ratio || 1).toFixed(1)}x</Mono></td>
                    <td style={{ padding: "9px 8px" }}><Pill label={SIGNAL_LABEL[sig] || sig} color={SC[sig] || T.textMuted} T={T} /></td>
                  </tr>
                );
              })}
              {!items.length && <tr><td colSpan={8} style={{ padding: "30px 8px", textAlign: "center", fontSize: 11, color: T.textMuted, fontFamily: "'Share Tech Mono', monospace" }}>NO LIVE WATCHLIST DATA</td></tr>}
            </tbody>
          </table>
        )}
      </div>
    </Card>
  );
}

// ─── EXECUTION QUEUE ─────────────────────────────────────────────────────────
function ExecutionQueuePanel({ orders, T }) {
  const { isMobile } = useBreakpoint();
  const pending = (orders || []).filter(o => ["PENDING", "OPEN", "TRIGGER PENDING"].includes((o.status || "").toUpperCase()));
  const recent = (orders || []).filter(o => ["COMPLETE", "FILLED"].includes((o.status || "").toUpperCase())).slice(0, 8);
  return (
    <Card T={T} accent={T.amber}>
      <CardHeader T={T} title="Execution Queue" subtitle="Pending orders & recent fills" accent={T.amber}
        right={
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {pending.length > 0 && <Pill label={`${pending.length} pending`} color={T.amber} T={T} />}
            <Pill label="live" color={T.green} T={T} />
          </div>
        }
      />
      <div style={{ padding: "10px 16px 14px" }}>
        {pending.map((o, i) => {
          const age = o.placed_at ? Math.round((Date.now() - new Date(o.placed_at).getTime()) / 1000) : 0;
          return (
            <div key={i} style={{ display: "flex", gap: 8, alignItems: "center", padding: "9px 12px", background: T.bg, borderRadius: 2, marginBottom: 4, borderLeft: `2px solid ${T.amber}`, flexWrap: "wrap" }}>
              <span style={{ width: 6, height: 6, borderRadius: "50%", background: T.amber, flexShrink: 0, animation: "pulse-dot 1.5s infinite" }} />
              <span style={{ fontWeight: 700, fontSize: 11, color: T.text, minWidth: 70, fontFamily: "'Share Tech Mono', monospace" }}>{o.symbol}</span>
              <Pill label={o.side} color={o.side === "BUY" ? T.green : T.red} T={T} />
              <Mono size={10} color={T.textSub}>{o.quantity} @ {o.price ? `₹${o.price}` : `₹${o.trigger_price} TRG`}</Mono>
              <span style={{ marginLeft: "auto", fontSize: 9.5, color: T.textMuted, fontFamily: "'Share Tech Mono', monospace" }}>{age}s</span>
            </div>
          );
        })}
        <div style={{ fontSize: 8.5, color: T.textMuted, letterSpacing: 1.5, textTransform: "uppercase", marginBottom: 8, fontFamily: "'Share Tech Mono', monospace" }}>Recent Fills</div>
        {recent.slice(0, 5).map((o, i) => {
          const slip = o.price && o.average_price ? ((o.average_price - o.price) / o.price * 100) : null;
          return (
            <div key={i} style={{ display: "flex", gap: 8, alignItems: "center", padding: "7px 12px", background: T.bg, borderRadius: 2, marginBottom: 3, flexWrap: "wrap", borderLeft: `2px solid ${T.green}40` }}>
              <CheckCircle size={10} color={T.green} />
              <span style={{ fontWeight: 700, fontSize: 11, color: T.text, minWidth: 70, fontFamily: "'Share Tech Mono', monospace" }}>{o.symbol}</span>
              <Pill label={o.side} color={o.side === "BUY" ? T.green : T.red} T={T} />
              <Mono size={10} color={T.text}>₹{o.average_price?.toLocaleString("en-IN") || o.price?.toLocaleString("en-IN") || "MKT"}</Mono>
              {slip !== null && <Mono size={10} color={Math.abs(slip) > 0.1 ? T.amber : T.green}>{slip >= 0 ? "+" : ""}{slip.toFixed(3)}% slip</Mono>}
              <span style={{ marginLeft: "auto", fontSize: 9, color: T.textMuted, fontFamily: "'Share Tech Mono', monospace" }}>{o.placed_at ? new Date(o.placed_at).toLocaleTimeString("en-IN") : "—"}</span>
            </div>
          );
        })}
        {!recent.length && <div style={{ fontSize: 11, color: T.textMuted, paddingTop: 4, fontFamily: "'Share Tech Mono', monospace" }}>No fills today</div>}
      </div>
    </Card>
  );
}

// ─── INTRADAY CHART ──────────────────────────────────────────────────────────
function IntradayChartPanel({ ticks, tickHistory, T }) {
  const { isMobile } = useBreakpoint();
  const [symbol, setSymbol] = useState("NIFTY");
  const symbols = ["NIFTY", "BANKNIFTY", "RELIANCE", "HDFCBANK", "TCS", "SBIN"];
  const chartData = useMemo(() => {
    const series = tickHistory?.[symbol] || [];
    if (series.length < 2) return [];
    return series.map((v, i) => ({ time: `T-${series.length - i}`, open: v * 0.999, close: v, high: v * 1.001, low: v * 0.998, volume: Math.round(Math.random() * 50000) }));
  }, [symbol, tickHistory]);
  const dayChange = chartData.length > 1 ? ((chartData.at(-1).close - chartData[0].open) / chartData[0].open * 100) : 0;
  const color = dayChange >= 0 ? T.green : T.red;
  return (
    <Card T={T} accent={color}>
      <CardHeader T={T} title="Intraday Chart" subtitle="15-min OHLCV" accent={color}
        right={
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
            {symbols.map(s => (
              <button key={s} onClick={() => setSymbol(s)} style={{
                background: symbol === s ? `${T.accent}18` : "transparent",
                border: `1px solid ${symbol === s ? T.accent : T.border}`, borderRadius: 2,
                padding: "3px 8px", cursor: "pointer", fontSize: 9, color: symbol === s ? T.accent : T.textMuted,
                fontFamily: "'Share Tech Mono', monospace", fontWeight: 700, transition: "all 0.15s",
                boxShadow: symbol === s ? `0 0 8px ${T.accent}30` : "none",
              }}>{s}</button>
            ))}
          </div>
        }
      />
      <div style={{ padding: "10px 16px 14px" }}>
        {chartData.length === 0 ? (
          <div style={{ height: 180, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11, color: T.textMuted, background: T.bg, borderRadius: 3, border: `1px dashed ${T.border}`, fontFamily: "'Share Tech Mono', monospace" }}>
            No live ticks for {symbol}
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={isMobile ? 150 : 190}>
            <ComposedChart data={chartData} margin={{ top: 5, right: 5, bottom: 0, left: 0 }}>
              <CartesianGrid strokeDasharray="2 2" stroke={T.border} />
              <XAxis dataKey="time" tick={{ fill: T.textMuted, fontSize: 8, fontFamily: "'Share Tech Mono', monospace" }} tickLine={false} axisLine={false} interval={8} />
              <YAxis yAxisId="p" domain={["auto", "auto"]} tick={{ fill: T.textMuted, fontSize: 9, fontFamily: "'Share Tech Mono', monospace" }} tickLine={false} axisLine={false} tickFormatter={v => v.toFixed(0)} width={46} />
              <YAxis yAxisId="v" orientation="right" tick={{ fill: T.textMuted, fontSize: 8, fontFamily: "'Share Tech Mono', monospace" }} tickLine={false} axisLine={false} tickFormatter={v => `${(v / 1000).toFixed(0)}K`} width={34} />
              <Tooltip {...tooltipStyle(T)} formatter={(v, n) => [n === "volume" ? `${(v / 1000).toFixed(1)}K` : v.toFixed(2), n]} />
              <Bar yAxisId="v" dataKey="volume" fill={T.purple} opacity={0.18} radius={[1, 1, 0, 0]} />
              <Line yAxisId="p" type="monotone" dataKey="close" stroke={color} strokeWidth={2} dot={false} style={{ filter: `drop-shadow(0 0 4px ${color})` }} />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </div>
    </Card>
  );
}

// ─── BOTTOM NAV ───────────────────────────────────────────────────────────────
function BottomNav({ tabs, active, onChange, T }) {
  return (
    <div style={{
      position: "fixed", bottom: 0, left: 0, right: 0, zIndex: 200,
      background: T.card, borderTop: `1px solid ${T.border}`,
      display: "flex", overflowX: "auto", WebkitOverflowScrolling: "touch",
      scrollbarWidth: "none", boxShadow: `0 -4px 20px rgba(0,0,0,0.5)`,
    }}>
      {tabs.map(tab => {
        const Icon = tab.icon;
        const isActive = active === tab.id;
        return (
          <button key={tab.id} onClick={() => onChange(tab.id)} style={{
            flex: "0 0 auto", background: "none", border: "none", cursor: "pointer",
            padding: "10px 14px", display: "flex", flexDirection: "column", alignItems: "center", gap: 3,
            color: isActive ? T.accent : T.textMuted,
            borderTop: `2px solid ${isActive ? T.accent : "transparent"}`,
            minWidth: 54, transition: "all 0.15s",
          }}>
            <Icon size={14} style={{ filter: isActive ? `drop-shadow(0 0 4px ${T.accent})` : "none" }} />
            <span style={{ fontSize: 7.5, fontWeight: 700, letterSpacing: 1, whiteSpace: "nowrap", fontFamily: "'Share Tech Mono', monospace" }}>{tab.label.toUpperCase()}</span>
          </button>
        );
      })}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// ─── SIMULATOR TAB (FULL LIVE EXPERIENCE) ────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════════

// Synthetic price data generator for demo
function genPriceSeries(base, len = 150, vol = 0.015) {
  let prices = [base];
  let trend = (Math.random() - 0.5) * 0.002;
  for (let i = 1; i < len; i++) {
    const drift = trend + (Math.random() - 0.5) * vol;
    if (Math.random() < 0.1) trend = (Math.random() - 0.5) * 0.002;
    prices.push(Math.max(50, prices[i - 1] * (1 + drift)));
  }
  return prices;
}

const BASE_PRICES = { RELIANCE: 2450, TCS: 3800, INFY: 1650, HDFCBANK: 1520, SBIN: 580, AXISBANK: 1120, WIPRO: 480, TATAMOTORS: 850, BAJFINANCE: 6800 };
const SIM_SYMBOLS = Object.keys(BASE_PRICES);

// RSI calc
function calcRsi(prices, n = 14) {
  if (prices.length < n + 1) return 50;
  let gains = 0, losses = 0;
  for (let i = prices.length - n; i < prices.length; i++) {
    const d = prices[i] - prices[i - 1];
    if (d > 0) gains += d; else losses -= d;
  }
  if (losses === 0) return 100;
  return 100 - 100 / (1 + gains / losses);
}

function calcMacd(prices) {
  if (prices.length < 26) return "neutral";
  const ema = (arr, n) => arr.reduce((a, v, i) => i === 0 ? v : a * (1 - 2 / (n + 1)) + v * 2 / (n + 1), arr[0]);
  return ema(prices.slice(-12), 12) > ema(prices.slice(-26), 26) ? "bullish" : "bearish";
}

const AI_THOUGHTS = [
  "Analyzing OHLCV data for RSI divergence on {SYM}…",
  "MACD crossover detected on <strong>{SYM}</strong> — bullish momentum building",
  "Volume spike {VOL}x above 20-day average on <strong>{SYM}</strong>",
  "Supertrend indicator flipped bullish — confirming long bias",
  "Checking open positions vs capital allocation limits…",
  "RSI oversold at {RSI} on <strong>{SYM}</strong> — potential reversal zone",
  "Options chain PCR at {PCR} — net bullish sentiment detected",
  "Generating BUY signal for <strong>{SYM}</strong> with confidence {CONF}",
  "Risk check: position size within {PCT}% of capital — approved ✓",
  "VIX elevated — reducing position sizing by 20%",
  "BB squeeze detected on <strong>{SYM}</strong> — breakout imminent, waiting for direction",
  "Executing MARKET order: <strong>{SYM} ×{QTY} @ ₹{PRICE}</strong>",
  "Stop-loss placed at ₹<strong>{SL}</strong> ({SLPCT}% risk)",
  "Target set at ₹<strong>{TGT}</strong> (R/R = {RR}:1)",
  "EMA crossover: 9-day above 21-day on <strong>{SYM}</strong>",
  "No high-conviction setups this candle — HOLD recommended",
  "Market regime classified as <strong>{REGIME}</strong>",
  "Reviewing open P&L — all positions within SL range",
  "Confidence filter: {REJECTED} weak signals rejected below threshold",
  "Backtest cross-validates this pattern: {WINRATE}% win rate historically",
];

const REGIMES = ["trending_up", "trending_down", "ranging", "high_volatility"];
const REGIME_COMMENTARY = {
  trending_up: ["Strong uptrend confirmed by EMA stack — momentum plays preferred", "Price above VWAP with expanding BB — trend continuation likely"],
  trending_down: ["Bearish regime — short bias only, tight stops", "EMA death cross present — avoid long entries"],
  ranging: ["Choppy market — mean reversion setups preferred", "PCR neutral — no directional conviction from options market"],
  high_volatility: ["VIX elevated — reducing position sizes by 30%", "High fear regime — favour short-premium strategies"],
};

function SimulatorTab({ T, simState, simConfig, setSimConfig, loadSimulation, backfillAndRun, simBackfilling, onResumeRun }) {
  const { isMobile } = useBreakpoint();
  const [liveSimRunning, setLiveSimRunning] = useState(false);
  const [liveSimPaused, setLiveSimPaused] = useState(false);
  const resolvedRecommendations = simState.selectionSummary?.recommendations || [];
  const autoSelectedSymbols = useMemo(() => resolvedRecommendations.map(item => item.symbol).filter(Boolean), [resolvedRecommendations]);
  const selectedSymbols = useMemo(() => (simConfig.selection_mode === "auto" ? autoSelectedSymbols : normalizeSymbols(simConfig.symbols)), [autoSelectedSymbols, simConfig.selection_mode, simConfig.symbols]);
  const symbolPool = selectedSymbols.length ? selectedSymbols : SIM_SYMBOLS;
  const [chartSymbol, setChartSymbol] = useState(() => selectedSymbols[0] || SIM_SYMBOLS[0]);

  // Live sim state
  const [simLive, setSimLive] = useState({
    candle: 0, totalCandles: 100,
    equity: 100000, equityHistory: [100000],
    date: new Date("2024-01-02"),
    tradeLog: [], positions: {}, openSignals: [],
    decisions: 0, signalCount: 0,
    wins: 0, losses: 0, maxEquity: 100000, maxDrawdown: 0,
    stage: null, progressPct: 0,
    regime: "—", commentary: "",
    thoughts: [], strategyWeights: { momentum: 0.30, mean_reversion: 0.25, options_selling: 0.20, breakout: 0.15, scalping: 0.10 },
    priceData: null,
  });

  const hasReplayData = Array.isArray(simState.data?.equity_curve) && simState.data.equity_curve.length > 0;
  const hasLiveReplay = Boolean(simState.liveReplay?.equityHistory?.length);
  const replayActive = simState.loading || hasReplayData || hasLiveReplay;

  const replayDerived = useMemo(() => {
    if (simState.loading && simState.liveReplay) {
      const snapshot = simState.liveReplay;
      return {
        ...snapshot,
        date: snapshot?.date ? new Date(snapshot.date) : new Date(),
        tradeLog: (snapshot?.tradeLog || []).map(t => ({
          ...t,
          time: t?.time ? new Date(t.time).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }) : "—",
        })),
      };
    }
    if (!hasReplayData) return null;
    const equityCurve = simState.data?.equity_curve || [];
    const trades = [...(simState.data?.trades || [])].sort((a, b) => String(a.timestamp || "").localeCompare(String(b.timestamp || "")));
    const lastPoint = equityCurve[equityCurve.length - 1] || {};
    const lastDate = lastPoint.timestamp ? new Date(lastPoint.timestamp) : new Date();
    const wins = trades.filter(t => Number(t.pnl || 0) > 0).length;
    const losses = trades.filter(t => Number(t.pnl || 0) < 0).length;
    const positions = {};
    for (const t of trades) {
      const action = String(t.action || "").toUpperCase();
      if (action === "BUY") positions[t.symbol] = { side: "BUY", entry: Number(t.price || 0), qty: Number(t.quantity || 0) };
      if (action === "SELL") delete positions[t.symbol];
    }
    const thoughts = trades.slice(-25).map(t => ({
      timestamp: t.timestamp,
      level: String(t.action || "").toUpperCase() === "BUY" ? "success" : "info",
      message: `${String(t.action || "").toUpperCase()} <strong>${t.symbol || ""}</strong> @ ₹${Math.round(Number(t.price || 0)).toLocaleString("en-IN")} · Qty ${Number(t.quantity || 0)}`,
    }));
    return {
      candle: equityCurve.length,
      totalCandles: equityCurve.length,
      equity: Number(lastPoint.equity || simConfig.initial_capital || 100000),
      equityHistory: equityCurve.map(p => Number(p.equity || 0)),
      date: lastDate,
      tradeLog: [...(simState.data?.trades || [])].sort((a, b) => String(b.timestamp || "").localeCompare(String(a.timestamp || ""))).map(t => ({
        symbol: t.symbol,
        action: String(t.action || "").toUpperCase(),
        price: Number(t.price || 0),
        quantity: Number(t.quantity || 0),
        pnl: t.pnl == null ? null : Number(t.pnl),
        time: t.timestamp ? new Date(t.timestamp).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }) : "—",
      })),
      positions,
      openSignals: [],
      decisions: Number(simState.data?.summary?.order_count || 0),
      signalCount: Number(simState.data?.summary?.order_count || 0),
      wins,
      losses,
      maxEquity: Math.max(...equityCurve.map(p => Number(p.equity || 0))),
      maxDrawdown: Number(simState.data?.summary?.drawdown_pct || 0),
      stage: simState.loading ? "placing_orders" : "decision_complete",
      progressPct: Number(simState.progress?.pct || 100),
      regime: "replay_backtest",
      commentary: `Replay window: ${simConfig.start_date || "(open)"} → ${simConfig.end_date || "(open)"}`,
      thoughts,
      strategyWeights: { momentum: 0.25, mean_reversion: 0.25, options_selling: 0.2, breakout: 0.2, scalping: 0.1 },
      priceData: simState.liveReplay?.priceData || null,
    };
  }, [hasReplayData, simConfig.end_date, simConfig.initial_capital, simConfig.start_date, simState.data, simState.liveReplay, simState.loading, simState.progress?.pct]);

  useEffect(() => {
    if (!replayDerived) return;
    if (intervalRef.current) { clearInterval(intervalRef.current); intervalRef.current = null; }
    setLiveSimRunning(false);
    setLiveSimPaused(false);
    setSimLive(prev => ({ ...prev, ...replayDerived }));
  }, [replayDerived]);

  const intervalRef = useRef(null);
  const priceDataRef = useRef(null);

  useEffect(() => {
    if (!symbolPool.includes(chartSymbol)) setChartSymbol(symbolPool[0]);
  }, [chartSymbol, symbolPool]);

  // Init price data
  useEffect(() => {
    const data = {};
    symbolPool.forEach((s, idx) => {
      const fallbackBase = 1000 + idx * 250;
      data[s] = genPriceSeries(BASE_PRICES[s] || fallbackBase, 120);
    });
    priceDataRef.current = data;
    setSimLive(prev => ({ ...prev, priceData: data }));
  }, [symbolPool]);

  const fillThought = useCallback((tpl, sym, candle) => {
    const prices = priceDataRef.current?.[sym] || [];
    const price = prices[Math.min(candle, prices.length - 1)] || 2500;
    const rsi = calcRsi(prices.slice(0, candle + 1));
    return tpl
      .replace(/{SYM}/g, sym)
      .replace(/{PRICE}/g, price.toFixed(0))
      .replace(/{RSI}/g, rsi.toFixed(1))
      .replace(/{QTY}/g, Math.round(5 + Math.random() * 25))
      .replace(/{SL}/g, (price * 0.982).toFixed(0))
      .replace(/{SLPCT}/g, "1.8")
      .replace(/{TGT}/g, (price * 1.04).toFixed(0))
      .replace(/{RR}/g, (1.8 + Math.random()).toFixed(1))
      .replace(/{VOL}/g, (1.3 + Math.random() * 1.5).toFixed(1))
      .replace(/{PCR}/g, (0.8 + Math.random() * 0.7).toFixed(2))
      .replace(/{CONF}/g, (0.6 + Math.random() * 0.3).toFixed(2))
      .replace(/{PCT}/g, (2 + Math.random() * 3).toFixed(1))
      .replace(/{REGIME}/g, REGIMES[Math.floor(Math.random() * REGIMES.length)].replace("_", " "))
      .replace(/{REJECTED}/g, Math.round(Math.random() * 3))
      .replace(/{WINRATE}/g, Math.round(52 + Math.random() * 20));
  }, []);

  const pushThought = useCallback((text, candle, level = "info") => {
    const sym = symbolPool[Math.floor(Math.random() * symbolPool.length)] || symbolPool[0] || "RELIANCE";
    const filled = fillThought(text, sym, candle);
    const entry = {
      timestamp: new Date().toISOString(),
      message: filled,
      level,
    };
    setSimLive(prev => ({
      ...prev,
      thoughts: [entry, ...prev.thoughts].slice(0, 20),
    }));
  }, [fillThought, symbolPool]);

  const stopLiveSim = useCallback(() => {
    if (intervalRef.current) { clearInterval(intervalRef.current); intervalRef.current = null; }
    setLiveSimRunning(false);
    setLiveSimPaused(false);
  }, []);

  const stepSim = useCallback(() => {
    setSimLive(prev => {
      if (prev.candle >= prev.totalCandles) { stopLiveSim(); return prev; }

      const sym = symbolPool[Math.floor(Math.random() * symbolPool.length)] || symbolPool[0] || "RELIANCE";
      const prices = priceDataRef.current?.[sym] || [];
      const price = prices[Math.min(prev.candle, prices.length - 1)] || 2500;
      const rsi = calcRsi(prices.slice(0, prev.candle + 1));
      const macd = calcMacd(prices.slice(0, prev.candle + 1));
// Advance date
      const newDate = new Date(prev.date);
      newDate.setDate(newDate.getDate() + 1);
      if (newDate.getDay() === 0) newDate.setDate(newDate.getDate() + 1);
      if (newDate.getDay() === 6) newDate.setDate(newDate.getDate() + 2);

      // Stage progression
      const stages = ["collecting_context", "calling_model", "risk_checks", "placing_orders", "decision_complete"];
      const stageIdx = Math.floor((prev.candle % 5));
      const stage = stages[stageIdx] || "decision_complete";
      const progressPct = Math.round(((stageIdx + 1) / stages.length) * 100);

      // Occasionally change regime
      const regime = Math.random() > 0.85 ? REGIMES[Math.floor(Math.random() * REGIMES.length)] : (prev.regime === "—" ? "ranging" : prev.regime);
      const commentary = REGIME_COMMENTARY[regime]?.[Math.floor(Math.random() * 2)] || "";

      // Adjust strategy weights
      let newWeights = { ...prev.strategyWeights };
      if (Math.random() > 0.7) {
        const keys = Object.keys(newWeights);
        const k = keys[Math.floor(Math.random() * keys.length)];
        newWeights[k] = Math.max(0.05, Math.min(0.5, newWeights[k] + (Math.random() - 0.5) * 0.06));
        const total = Object.values(newWeights).reduce((a, b) => a + b, 0);
        Object.keys(newWeights).forEach(key => newWeights[key] /= total);
      }

      // Generate signals
      const numSignals = Math.random() < 0.35 ? 0 : Math.random() < 0.55 ? 1 : 2;
      let newSignals = [];
      for (let i = 0; i < numSignals; i++) {
        const sigSym = symbolPool[Math.floor(Math.random() * symbolPool.length)] || symbolPool[0] || "RELIANCE";
        const sigPrice = (priceDataRef.current?.[sigSym] || [])[Math.min(prev.candle, 119)] || 2500;
        const action = Math.random() > 0.45 ? "BUY" : "SELL";
        const slPct = 0.015 + Math.random() * 0.01;
        const tgtPct = slPct * (1.5 + Math.random());
        newSignals.push({
          symbol: sigSym, action, confidence: 0.55 + Math.random() * 0.35,
          strategy: ["momentum", "breakout", "mean_reversion", "scalping"][Math.floor(Math.random() * 4)],
          entry_price: sigPrice.toFixed(0),
          stop_loss: (sigPrice * (action === "BUY" ? 1 - slPct : 1 + slPct)).toFixed(0),
          target: (sigPrice * (action === "BUY" ? 1 + tgtPct : 1 - tgtPct)).toFixed(0),
          risk_reward: (tgtPct / slPct).toFixed(1),
          rationale: [
            `RSI ${rsi.toFixed(1)} with MACD ${action === "BUY" ? "bullish" : "bearish"} crossover on daily chart`,
            `Volume ${(1.3 + Math.random() * 1.5).toFixed(1)}x avg; breaking ${action === "BUY" ? "above" : "below"} key resistance`,
            `Supertrend ${action === "BUY" ? "bullish" : "bearish"} + price ${action === "BUY" ? "above" : "below"} VWAP — strong bias`,
          ][Math.floor(Math.random() * 3)],
        });
      }

      // Execute trades
      let newPositions = { ...prev.positions };
      let newEquity = prev.equity;
      let newWins = prev.wins, newLosses = prev.losses;
      const newTradeLog = [...prev.tradeLog];

      const nowStr = newDate.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });

      newSignals.forEach(sig => {
        const sp = parseFloat(sig.entry_price);
        const qty = Math.round(5 + Math.random() * 20);
        if (sig.action === "BUY" && newEquity > sp * qty * 1.1) {
          newPositions[sig.symbol] = { side: "BUY", entry: sp, qty };
          newEquity -= sp * qty;
          newTradeLog.unshift({ symbol: sig.symbol, action: "BUY", price: sp, quantity: qty, pnl: null, time: nowStr });
        } else if (sig.action === "SELL" && newPositions[sig.symbol]) {
          const pos = newPositions[sig.symbol];
          const exitPrice = (priceDataRef.current?.[sig.symbol] || [])[Math.min(prev.candle, 119)] || sp;
          const pnl = (exitPrice - pos.entry) * pos.qty;
          newEquity += exitPrice * pos.qty;
          if (pnl >= 0) newWins++; else newLosses++;
          newTradeLog.unshift({ symbol: sig.symbol, action: "SELL", price: exitPrice, quantity: pos.qty, pnl, time: nowStr });
          delete newPositions[sig.symbol];
        }
      });

      // Occasionally close position (SL/target)
      const posKeys = Object.keys(newPositions);
      if (posKeys.length > 0 && Math.random() > 0.82) {
        const closeSym = posKeys[Math.floor(Math.random() * posKeys.length)];
        const pos = newPositions[closeSym];
        const closePrice = (priceDataRef.current?.[closeSym] || [])[Math.min(prev.candle, 119)] || pos.entry;
        const pnl = (closePrice - pos.entry) * pos.qty;
        newEquity += closePrice * pos.qty;
        if (pnl >= 0) newWins++; else newLosses++;
        newTradeLog.unshift({ symbol: closeSym, action: "SELL", price: closePrice, quantity: pos.qty, pnl, time: nowStr, reason: pnl >= 0 ? "TARGET" : "SL" });
        delete newPositions[closeSym];
      }

      const newMaxEquity = Math.max(prev.maxEquity, newEquity);
      const dd = ((newMaxEquity - newEquity) / newMaxEquity) * 100;
      const newMaxDD = Math.max(prev.maxDrawdown, dd);
      const newEquityHistory = [...prev.equityHistory, newEquity].slice(-120);

      // Thoughts
      const thoughtsToAdd = [];
      if (Math.random() > 0.45) {
        const t = AI_THOUGHTS[Math.floor(Math.random() * AI_THOUGHTS.length)];
        thoughtsToAdd.push({ timestamp: new Date().toISOString(), message: fillThought(t, sym, prev.candle), level: "info" });
      }
      if (newSignals.length > 0) {
        thoughtsToAdd.push({
          timestamp: new Date().toISOString(),
          message: `Generated <strong>${newSignals.length}</strong> signal${newSignals.length > 1 ? "s" : ""}: ${newSignals.map(s => `${s.action} ${s.symbol}`).join(", ")}`,
          level: "success",
        });
      }

      return {
        ...prev,
        candle: prev.candle + 1,
        date: newDate,
        equity: newEquity,
        equityHistory: newEquityHistory,
        tradeLog: newTradeLog.slice(0, 50),
        positions: newPositions,
        openSignals: newSignals,
        decisions: prev.decisions + 1,
        signalCount: prev.signalCount + newSignals.length,
        wins: newWins,
        losses: newLosses,
        maxEquity: newMaxEquity,
        maxDrawdown: newMaxDD,
        stage,
        progressPct,
        regime,
        commentary,
        strategyWeights: newWeights,
        thoughts: [...thoughtsToAdd, ...prev.thoughts].slice(0, 25),
      };
    });
  }, [fillThought, stopLiveSim, symbolPool]);

  const startLiveSim = useCallback(() => {
    setSimLive(prev => ({
      ...prev, candle: 0, equity: 100000, equityHistory: [100000],
      date: new Date("2024-01-02"), tradeLog: [], positions: {}, openSignals: [],
      decisions: 0, signalCount: 0, wins: 0, losses: 0, maxEquity: 100000, maxDrawdown: 0,
      stage: null, progressPct: 0, thoughts: [], regime: "—",
    }));
    setLiveSimRunning(true);
    setLiveSimPaused(false);
    intervalRef.current = setInterval(stepSim, 700);
  }, [stepSim]);

  const togglePause = useCallback(() => {
    if (liveSimPaused) {
      intervalRef.current = setInterval(stepSim, 700);
      setLiveSimPaused(false);
    } else {
      if (intervalRef.current) { clearInterval(intervalRef.current); intervalRef.current = null; }
      setLiveSimPaused(true);
    }
  }, [liveSimPaused, stepSim]);

  useEffect(() => () => { if (intervalRef.current) clearInterval(intervalRef.current); }, []);

  const pnl = simLive.equity - 100000;
  const pnlColor = pnl >= 0 ? T.green : T.red;
  const totalTrades = simLive.wins + simLive.losses;
  const winRate = totalTrades > 0 ? Math.round(simLive.wins / totalTrades * 100) : 0;
  const returnPct = ((simLive.equity - 100000) / 100000 * 100);

  const equityChartData = simLive.equityHistory.map((v, i) => ({ i, equity: Math.round(v) }));

  const inputStyle = {
    background: T.bg, border: `1px solid ${T.border}`, color: T.text,
    borderRadius: 3, padding: "9px 12px", fontSize: 11,
    fontFamily: "'Share Tech Mono', monospace", outline: "none",
    transition: "border-color 0.15s",
  };
  const btn = (color) => ({
    background: `${color}12`, border: `1px solid ${color}40`, borderRadius: 3,
    padding: "7px 16px", cursor: "pointer", fontSize: 9.5, fontWeight: 700,
    color, letterSpacing: 1.5, fontFamily: "'Share Tech Mono', monospace",
    textTransform: "uppercase", transition: "all 0.15s",
  });

  const col2 = isMobile ? "1fr" : "1fr 1fr";
  const col3 = isMobile ? "1fr" : "1fr 1fr 1fr";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {/* ── TOP KPI ROW ── */}
      <div style={{ display: "grid", gridTemplateColumns: isMobile ? "repeat(2,1fr)" : "repeat(4,1fr)", gap: 10 }}>
        <StatTile T={T} label="Equity" value={`₹${Math.round(simLive.equity / 1000).toFixed(0)}K`}
          sub={<span style={{ color: pnlColor }}>{pnl >= 0 ? "+" : ""}₹{Math.abs(Math.round(pnl)).toLocaleString("en-IN")}</span>}
          color={pnlColor} icon={DollarSign}
        />
        <StatTile T={T} label="Return" value={`${returnPct >= 0 ? "+" : ""}${returnPct.toFixed(1)}%`}
          sub="vs ₹1,00,000 initial" color={returnPct >= 0 ? T.green : T.red} icon={TrendingUp}
        />
        <StatTile T={T} label="Win Rate"
          value={totalTrades > 0 ? `${winRate}%` : "—"}
          sub={`${simLive.wins}W / ${simLive.losses}L · ${totalTrades} trades`}
          color={winRate >= 50 ? T.green : T.red} icon={Target}
        />
        <StatTile T={T} label="Max Drawdown"
          value={`${simLive.maxDrawdown.toFixed(2)}%`}
          sub={`AI decisions: ${simLive.decisions} · ${simLive.signalCount} signals`}
          color={simLive.maxDrawdown > 5 ? T.red : T.amber} icon={AlertTriangle}
        />
      </div>

      {/* ── HUD BAR ── */}
      <div style={{
        display: "flex", alignItems: "center", gap: 12,
        padding: "10px 16px", background: T.card, border: `1px solid ${T.border}`,
        borderTop: `2px solid ${replayActive ? T.green : T.textMuted}`, flexWrap: "wrap",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <StatusDot active={replayActive && !simBackfilling} color={T.green} />
          <span style={{ fontSize: 9, letterSpacing: 2, textTransform: "uppercase", color: T.textMuted, fontFamily: "'Share Tech Mono', monospace" }}>
            {simBackfilling ? "BACKFILLING" : simState.loading ? "RUNNING" : hasReplayData ? "COMPLETED" : "IDLE"}
          </span>
        </div>
        {[
          { label: "Candle", value: `${simLive.candle}/${simLive.totalCandles}`, color: T.accent },
          { label: "Date", value: simLive.date.toLocaleDateString("en-IN", { day: "2-digit", month: "short" }), color: T.text },
          { label: "Open Pos", value: Object.keys(simLive.positions).length, color: T.purple },
          { label: "Cash", value: `₹${Math.round(simLive.equity / 1000).toFixed(0)}K`, color: T.text },
        ].map(item => (
          <div key={item.label} style={{ display: "flex", flexDirection: "column", gap: 1, paddingLeft: 14, borderLeft: `1px solid ${T.border}` }}>
            <span style={{ fontSize: 7.5, color: T.textDim, letterSpacing: 1.5, textTransform: "uppercase", fontFamily: "'Share Tech Mono', monospace" }}>{item.label}</span>
            <span style={{ fontSize: 13, fontWeight: 700, color: item.color, fontFamily: "'Share Tech Mono', monospace" }}>{item.value}</span>
          </div>
        ))}
        <div style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
          <span style={{ fontSize: 9, color: T.textMuted, letterSpacing: 1, fontFamily: "'Share Tech Mono', monospace" }}>
            Replay-driven view (Backfill & Run / Rerun)
          </span>
        </div>
      </div>

      {/* ── MAIN GRID ── */}
      <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "1fr 340px", gap: 14, alignItems: "start" }}>
        {/* LEFT */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>

          {/* EQUITY CURVE */}
          <Card T={T} accent={pnlColor}>
            <CardHeader T={T} title="Equity Curve" subtitle="Real-time P&L trajectory" accent={pnlColor}
              right={
                <span style={{ fontFamily: "'Share Tech Mono', monospace", fontSize: 14, fontWeight: 700, color: pnlColor }}>
                  {pnl >= 0 ? "+" : ""}₹{Math.abs(Math.round(pnl)).toLocaleString("en-IN")}
                </span>
              }
            />
            <div style={{ padding: "0 16px 12px" }}>
              <ResponsiveContainer width="100%" height={150}>
                <AreaChart data={equityChartData} margin={{ left: 0, right: 5, top: 10, bottom: 0 }}>
                  <defs>
                    <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={pnlColor} stopOpacity="0.25" />
                      <stop offset="100%" stopColor={pnlColor} stopOpacity="0" />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="2 2" stroke={T.border} />
                  <XAxis dataKey="i" hide />
                  <YAxis tick={{ fill: T.textMuted, fontSize: 9, fontFamily: "'Share Tech Mono', monospace" }} tickLine={false} axisLine={false} tickFormatter={v => `₹${(v / 1000).toFixed(0)}K`} width={44} />
                  <Tooltip {...tooltipStyle(T)} formatter={v => [`₹${Number(v).toLocaleString("en-IN")}`, "Equity"]} />
                  <ReferenceLine y={100000} stroke={T.border} strokeDasharray="4 2" />
                  <Area type="monotone" dataKey="equity" stroke={pnlColor} fill="url(#eqGrad)" strokeWidth={2} dot={false} style={{ filter: `drop-shadow(0 0 4px ${pnlColor})` }} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </Card>

          {/* CANDLE + INDICATOR ROW */}
          <div style={{ display: "grid", gridTemplateColumns: col2, gap: 12 }}>
            {/* CANDLE CHART */}
            <Card T={T} accent={T.amber}>
              <CardHeader T={T} title="Price Action" subtitle="OHLCV candlestick view" accent={T.amber}
                right={
                  <select
                    value={chartSymbol}
                    onChange={e => setChartSymbol(e.target.value)}
                    style={{ background: T.bg, border: `1px solid ${T.border}`, color: T.textSub, fontSize: 9, padding: "3px 6px", fontFamily: "'Share Tech Mono', monospace" }}
                  >
                    {symbolPool.map(s => <option key={s}>{s}</option>)}
                  </select>
                }
              />
              <SimCandleChart
                symbol={chartSymbol}
                candle={simLive.candle}
                priceData={simLive.priceData}
                T={T}
              />
            </Card>

            {/* SIGNAL MATRIX */}
            <Card T={T} accent={T.purple}>
              <CardHeader T={T} title="Indicator Matrix" subtitle="Live signal computation" accent={T.purple} />
              <div style={{ padding: "12px 14px" }}>
                {selectedSymbols.length === 0 ? (
                  <div style={{ color: T.textMuted, fontSize: 10, fontFamily: "'Share Tech Mono', monospace" }}>Add at least one symbol to view indicators.</div>
                ) : symbolPool.slice(0, 4).map(sym => {
                  const prices = (simLive.priceData?.[sym] || []).slice(0, simLive.candle + 1);
                  const rsi = calcRsi(prices);
                  const macd = calcMacd(prices);
                  const trend = prices.length > 20 ? (prices.at(-1) > prices.at(-20) ? "bullish" : "bearish") : "neutral";
                  const signal = rsi < 35 && macd === "bullish" ? "BUY" : rsi > 65 && macd === "bearish" ? "SELL" : "HOLD";
                  const sigColor = signal === "BUY" ? T.green : signal === "SELL" ? T.red : T.amber;
                  return (
                    <div key={sym} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 0", borderBottom: `1px solid ${T.border}20` }}>
                      <span style={{ minWidth: 70, fontSize: 10, fontWeight: 700, color: T.text, fontFamily: "'Share Tech Mono', monospace" }}>{sym}</span>
                      <Mono size={10} color={rsi < 35 ? T.green : rsi > 65 ? T.red : T.text}>{rsi.toFixed(0)}</Mono>
                      <div style={{ width: 40 }}><Pill label={macd} color={macd === "bullish" ? T.green : T.red} T={T} /></div>
                      <div style={{ marginLeft: "auto" }}><Pill label={signal} color={sigColor} T={T} /></div>
                    </div>
                  );
                })}
                <div style={{ marginTop: 10, padding: "8px 10px", background: T.bg, borderRadius: 2 }}>
                  <div style={{ fontSize: 8.5, color: T.textMuted, letterSpacing: 1.5, fontFamily: "'Share Tech Mono', monospace", marginBottom: 4 }}>MARKET REGIME</div>
                  <div style={{ fontSize: 13, fontWeight: 700, color: simLive.regime.includes("up") ? T.green : simLive.regime.includes("down") ? T.red : T.amber, fontFamily: "'Share Tech Mono', monospace" }}>
                    {simLive.regime.replace(/_/g, " ").toUpperCase()}
                  </div>
                  {simLive.commentary && <div style={{ fontSize: 9, color: T.textMuted, marginTop: 3 }}>{simLive.commentary}</div>}
                </div>
              </div>
            </Card>
          </div>

          {/* AI REASONING STREAM */}
          <Card T={T} accent={T.accent}>
            <CardHeader T={T} title="AI Reasoning Stream" subtitle="Live thought-by-thought decision log" accent={T.accent}
              right={
                simState.loading ? (
                  <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                    {[0, 1, 2].map(i => (
                      <span key={i} style={{
                        width: 4, height: 4, borderRadius: "50%", background: T.accent, display: "inline-block",
                        animation: `typing-bounce 1.2s infinite ${i * 0.2}s`,
                      }} />
                    ))}
                    <span style={{ fontSize: 8.5, color: T.textMuted, fontFamily: "'Share Tech Mono', monospace", marginLeft: 4, letterSpacing: 1 }}>PROCESSING</span>
                  </div>
                ) : null
              }
            />
            <div style={{ padding: "12px 14px" }}>
              {/* Stage Pipeline */}
              <StagePipeline currentStage={simLive.stage} T={T} />
              {/* Progress */}
              <div style={{ height: 3, background: T.bg, position: "relative", overflow: "hidden", marginBottom: 8, border: `1px solid ${T.border}` }}>
                <div style={{ position: "absolute", left: 0, top: 0, width: `${simLive.progressPct}%`, height: "100%", background: `linear-gradient(90deg, ${T.accent}80, ${T.accent})`, boxShadow: `0 0 8px ${T.accent}`, transition: "width 0.4s ease" }} />
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 10 }}>
                <span style={{ fontSize: 8, color: T.textMuted, fontFamily: "'Share Tech Mono', monospace" }}>Candle {simLive.candle} · {simLive.stage?.replace(/_/g, " ") || "idle"}</span>
                <span style={{ fontSize: 8, color: T.textMuted, fontFamily: "'Share Tech Mono', monospace" }}>{simLive.decisions} decisions · {simLive.signalCount} signals</span>
              </div>
              <ThoughtStream thoughts={simLive.thoughts} isThinking={simState.loading} T={T} />
            </div>
          </Card>

          {/* LIVE TRADE FEED */}
          <Card T={T} accent={T.teal || T.cyan}>
            <CardHeader T={T} title="Live Trade Feed" subtitle="All entries & exits as they happen" accent={T.cyan}
              right={<Pill label={`${simLive.tradeLog.length} trades`} color={T.cyan} T={T} />}
            />
            <div style={{ padding: "0 14px 12px" }}>
              <div style={{ maxHeight: 200, overflowY: "auto" }}>
                {simLive.tradeLog.length === 0 ? (
                  <div style={{ padding: "20px 0", color: T.textMuted, fontSize: 10, fontFamily: "'Share Tech Mono', monospace" }}>No trades yet — start the demo simulation above</div>
                ) : simLive.tradeLog.map((t, i) => <TradeRow key={i} trade={t} T={T} />)}
              </div>
            </div>
          </Card>
        </div>

        {/* RIGHT COLUMN */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>

          {/* OPEN POSITIONS */}
          <Card T={T} accent={T.green}>
            <CardHeader T={T} title="Open Positions" subtitle="Active sim positions" accent={T.green}
              right={<Pill label={`${Object.keys(simLive.positions).length} open`} color={T.green} T={T} />}
            />
            <div style={{ padding: "0 14px 8px" }}>
              {Object.entries(simLive.positions).map(([sym, pos]) => {
                const currPrice = (simLive.priceData?.[sym] || [])[Math.min(simLive.candle, 119)] || pos.entry;
                const pnl = (currPrice - pos.entry) * pos.qty;
                const pnlColor2 = pnl >= 0 ? T.green : T.red;
                return (
                  <div key={sym} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 0", borderBottom: `1px solid ${T.border}20` }}>
                    <div style={{ width: 2, height: 28, background: pnlColor2, flexShrink: 0 }} />
                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: 11, fontWeight: 700, color: T.text, fontFamily: "'Share Tech Mono', monospace" }}>{sym}</div>
                      <div style={{ fontSize: 9, color: T.textMuted }}>BUY ×{pos.qty} @ ₹{pos.entry.toFixed(0)}</div>
                    </div>
                    <div style={{ textAlign: "right" }}>
                      <div style={{ fontSize: 12, fontWeight: 700, color: pnlColor2, fontFamily: "'Share Tech Mono', monospace" }}>
                        {pnl >= 0 ? "+" : ""}₹{pnl.toFixed(0)}
                      </div>
                      <div style={{ fontSize: 9, color: T.textMuted }}>₹{currPrice.toFixed(0)}</div>
                    </div>
                  </div>
                );
              })}
              {Object.keys(simLive.positions).length === 0 && (
                <div style={{ padding: "16px 0", color: T.textMuted, fontSize: 10, fontFamily: "'Share Tech Mono', monospace", textAlign: "center" }}>No open positions</div>
              )}
            </div>
          </Card>

          {/* LATEST AI SIGNALS */}
          <Card T={T} accent={T.purple}>
            <CardHeader T={T} title="Latest AI Signals" subtitle="Most recent decisions" accent={T.purple} />
            <div style={{ padding: "12px 14px" }}>
              {simLive.openSignals.length === 0 ? (
                <div style={{ color: T.textMuted, fontSize: 10, fontFamily: "'Share Tech Mono', monospace" }}>Waiting for AI decisions…</div>
              ) : simLive.openSignals.slice(0, 3).map((s, i) => <SignalCard key={i} signal={s} T={T} />)}
            </div>
          </Card>

          {/* STRATEGY WEIGHTS */}
          <Card T={T} accent={T.cyan}>
            <CardHeader T={T} title="Strategy Mix" subtitle="Live weight allocation" accent={T.cyan} />
            <div style={{ padding: "12px 14px" }}>
              {Object.entries(simLive.strategyWeights).map(([k, v]) => {
                const STRAT_C = STRATEGY_COLORS_FN(T);
                const c = STRAT_C[k] || T.accent;
                return (
                  <div key={k} style={{ marginBottom: 10 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                      <span style={{ fontSize: 9, color: T.textSub, fontFamily: "'Share Tech Mono', monospace" }}>{k.replace(/_/g, " ")}</span>
                      <Mono size={9} color={c}>{(v * 100).toFixed(0)}%</Mono>
                    </div>
                    <ProgressBar value={v * 100} max={100} color={c} height={3} />
                  </div>
                );
              })}
            </div>
          </Card>

          {/* REGIME PANEL */}
          <Card T={T} accent={T.amber}>
            <CardHeader T={T} title="Market Regime" subtitle="AI classification" accent={T.amber} />
            <div style={{ padding: "12px 14px" }}>
              <div style={{ fontSize: 20, fontWeight: 700, color: simLive.regime.includes("up") ? T.green : simLive.regime.includes("down") ? T.red : T.amber, fontFamily: "'Share Tech Mono', monospace", letterSpacing: -0.5, marginBottom: 4 }}>
                {simLive.regime === "—" ? "—" : simLive.regime.replace(/_/g, " ").toUpperCase()}
              </div>
              {simLive.commentary && <p style={{ fontSize: 10, color: T.textMuted, lineHeight: 1.7, marginBottom: 10 }}>{simLive.commentary}</p>}
              {[
                { label: "Decisions", value: simLive.decisions },
                { label: "Signals fired", value: simLive.signalCount },
                { label: "Win rate", value: totalTrades > 0 ? `${winRate}%` : "—" },
                { label: "Drawdown", value: `${simLive.maxDrawdown.toFixed(2)}%` },
              ].map(row => (
                <div key={row.label} style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", borderBottom: `1px solid ${T.border}20`, fontSize: 10 }}>
                  <span style={{ color: T.textMuted, fontFamily: "'Share Tech Mono', monospace" }}>{row.label}</span>
                  <span style={{ color: T.text, fontWeight: 600 }}>{row.value}</span>
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>

      {/* ── DIVIDER ── */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 4 }}>
        <div style={{ flex: 1, height: 1, background: T.border }} />
        <span style={{ fontSize: 9, color: T.textMuted, letterSpacing: 2, textTransform: "uppercase", fontFamily: "'Share Tech Mono', monospace" }}>Real Backtest Engine</span>
        <div style={{ flex: 1, height: 1, background: T.border }} />
      </div>

      {/* ── BACKTEST CONFIG ── */}
      <Card T={T} accent={T.accent}>
        <CardHeader T={T} title="Historical Replay" subtitle={SIM_SOURCE} accent={T.accent}
          right={
            <div style={{ display: "flex", gap: 8 }}>
              <button onClick={backfillAndRun} disabled={simState.loading || simBackfilling} style={{ ...btn(T.accent), opacity: simState.loading || simBackfilling ? 0.5 : 1 }}>
                {simBackfilling ? "Backfilling…" : "Backfill & Run"}
              </button>
              <button onClick={loadSimulation} disabled={simState.loading || simBackfilling} style={{ ...btn(T.blue), opacity: simState.loading || simBackfilling ? 0.5 : 1 }}>
                Rerun
              </button>
            </div>
          }
        />
        <div style={{ padding: "14px 16px" }}>
          <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "repeat(3,1fr)", gap: 10, marginBottom: 10 }}>
            <select value={simConfig.selection_mode} onChange={e => setSimConfig(p => ({ ...p, selection_mode: e.target.value }))} style={inputStyle}>
              <option value="manual">Manual symbols</option>
              <option value="auto">Auto-pick by budget</option>
            </select>
            {simConfig.selection_mode === "manual" ? (
              <input type="text" placeholder="Symbols (comma-separated)" value={simConfig.symbols} onChange={e => setSimConfig(p => ({ ...p, symbols: e.target.value }))} style={inputStyle} />
            ) : (
              <>
                <input type="number" min="1" placeholder="Budget cap (₹)" value={simConfig.budget_cap} onChange={e => setSimConfig(p => ({ ...p, budget_cap: Number(e.target.value || 0) }))} style={inputStyle} />
                <input type="number" min="1" max="25" placeholder="Max auto symbols" value={simConfig.max_auto_symbols} onChange={e => setSimConfig(p => ({ ...p, max_auto_symbols: Math.max(1, Math.min(25, Math.round(Number(e.target.value || 1)))) }))} style={inputStyle} />
              </>
            )}
            {[
                            { placeholder: "Start date", key: "start_date", type: "date" },
              { placeholder: "End date", key: "end_date", type: "date" },
              { placeholder: "Initial capital", key: "initial_capital", type: "number" },
              { placeholder: "Fee %", key: "fee_pct", type: "number", step: "0.0001" },
              { placeholder: "Slippage %", key: "slippage_pct", type: "number", step: "0.0001" },
              { placeholder: "AI every N candles", key: "ai_every_n_candles", type: "number" },
              { placeholder: "Confidence threshold", key: "confidence_threshold", type: "number", step: "0.01" },
            ].map(f => (
              <input key={f.key} type={f.type} step={f.step} placeholder={f.placeholder}
                value={simConfig[f.key]}
                onChange={e => setSimConfig(p => {
                  if (f.type !== "number") return { ...p, [f.key]: e.target.value };
                  const numeric = Number(e.target.value || 0);
                  if (f.key === "ai_every_n_candles") return { ...p, [f.key]: Math.max(1, Math.round(numeric)) };
                  if (f.key === "confidence_threshold") return { ...p, [f.key]: Math.max(0.3, Math.min(0.95, numeric)) };
                  return { ...p, [f.key]: numeric };
                })}
                style={inputStyle}
              />
            ))}
          </div>

          {simConfig.selection_mode === "auto" && (
            <div style={{ background: T.accentDim, border: `1px solid ${T.accent}30`, borderLeft: `3px solid ${T.accent}`, borderRadius: 3, padding: "10px 14px", marginBottom: 10 }}>
              <div style={{ fontSize: 11, color: T.text, marginBottom: 4, fontFamily: "'Share Tech Mono', monospace" }}>AI auto-pick mode</div>
              <div style={{ fontSize: 10, color: T.textMuted }}>Enter a rupee budget and the backend will rank symbols using historical prices from the selected replay period, then estimate quantity, cost, and potential profit before replay starts.</div>
            </div>
          )}

          {simBackfilling && <div style={{ fontSize: 11, color: T.textMuted, marginBottom: 8, fontFamily: "'Share Tech Mono', monospace" }}>Backfilling historical candles…</div>}
          {simState.loading && (
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 11, color: T.textMuted, marginBottom: 4, fontFamily: "'Share Tech Mono', monospace" }}>
                Running replay… ({simState.runStatus || "queued"})
              </div>
              {simState.progress && (
                <>
                  <ProgressBar value={simState.progress.pct} max={100} color={T.accent} height={3} />
                  <div style={{ fontSize: 9, color: T.textMuted, marginTop: 4, fontFamily: "'Share Tech Mono', monospace" }}>
                    {simState.progress.processed}/{simState.progress.total} candles · {simState.progress.pct}%
                  </div>
                </>
              )}
            </div>
          )}

          {simState.selectionSummary?.selected_symbols?.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8, gap: 8, flexWrap: "wrap" }}>
                <div style={{ fontSize: 10, color: T.textMuted, fontFamily: "'Share Tech Mono', monospace" }}>Selected symbols: {simState.selectionSummary.selected_symbols.join(", ")}</div>
                {simState.selectionSummary?.budget_cap ? <div style={{ fontSize: 10, color: T.textMuted, fontFamily: "'Share Tech Mono', monospace" }}>Budget {formatRupees(simState.selectionSummary.budget_cap)}</div> : null}
              </div>
              <div style={{ display: "grid", gap: 8 }}>
                {simState.selectionSummary.recommendations.map((item) => (
                  <div key={item.symbol} style={{ border: `1px solid ${T.border}`, background: T.bg, padding: "10px 12px", borderRadius: 3 }}>
                    <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr 1fr" : "1.2fr repeat(5, 1fr)", gap: 8, alignItems: "center" }}>
                      <div>
                        <div style={{ fontSize: 11, color: T.text, fontWeight: 700, fontFamily: "'Share Tech Mono', monospace" }}>{item.symbol}</div>
                        <div style={{ fontSize: 9, color: T.textMuted }}>Score {Number(item.score || 0).toFixed(2)}</div>
                      </div>
                      <div style={{ fontSize: 10, color: T.textSub }}>LTP <strong style={{ color: T.text }}>{formatRupees(item.ltp)}</strong></div>
                      <div style={{ fontSize: 10, color: T.textSub }}>Qty <strong style={{ color: T.text }}>{item.estimated_qty}</strong></div>
                      <div style={{ fontSize: 10, color: T.textSub }}>Cost <strong style={{ color: T.text }}>{formatRupees(item.estimated_cost)}</strong></div>
                      <div style={{ fontSize: 10, color: item.estimated_profit_rupees >= 0 ? T.green : T.red }}>Est. profit <strong>{formatRupees(item.estimated_profit_rupees)}</strong></div>
                      <div style={{ fontSize: 10, color: T.textSub }}>Return <strong style={{ color: T.text }}>{Number(item.expected_return_pct || 0).toFixed(2)}%</strong></div>
                    </div>
                    <div style={{ fontSize: 9, color: T.textMuted, marginTop: 6 }}>{item.reason}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {simState.error && (
            <div style={{ background: T.redDim, border: `1px solid ${T.red}30`, borderLeft: `3px solid ${T.red}`, borderRadius: 3, padding: "10px 14px", marginBottom: 10 }}>
              <div style={{ fontSize: 11, color: T.red, marginBottom: 4, fontFamily: "'Share Tech Mono', monospace" }}>Error: {simState.error}</div>
              <div style={{ fontSize: 10, color: T.textMuted }}>Tip: backfill candles for the same symbols/date range, then click Rerun.</div>
            </div>
          )}

          {/* RESULTS */}
          {simState.data?.equity_curve?.length > 0 && (
            <>
              <div style={{ display: "grid", gridTemplateColumns: isMobile ? "repeat(2,1fr)" : "repeat(5,1fr)", gap: 10, marginBottom: 14 }}>
                <StatTile T={T} label="Final Value" value={`₹${Math.round(simState.data.summary.final_value || 0).toLocaleString("en-IN")}`} color={T.accent} />
                <StatTile T={T} label="Net P&L"
                  value={<span style={{ color: (simState.data.summary.net_pnl || 0) >= 0 ? T.green : T.red, fontFamily: "'Share Tech Mono', monospace", fontSize: 18, fontWeight: 700 }}>
                    {(simState.data.summary.net_pnl || 0) >= 0 ? "+" : ""}₹{Math.abs(simState.data.summary.net_pnl || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 })}
                  </span>}
                  color={T.green}
                />
                <StatTile T={T} label="Drawdown" value={`${(simState.data.summary.drawdown_pct || 0).toFixed(2)}%`} color={T.red} />
                <StatTile T={T} label="Win Rate" value={`${(simState.data.summary.win_rate || 0).toFixed(1)}%`} color={T.amber} />
                <StatTile T={T} label="Total Trades" value={simState.data.summary.trade_count || 0} color={T.purple} />
              </div>
              <ResponsiveContainer width="100%" height={200}>
                <LineChart data={(simState.data.equity_curve || []).map(x => ({ ...x, date: x.timestamp?.slice(0, 10) }))} margin={{ left: 0 }}>
                  <CartesianGrid strokeDasharray="2 2" stroke={T.border} />
                  <XAxis dataKey="date" tick={{ fill: T.textMuted, fontSize: 9, fontFamily: "'Share Tech Mono', monospace" }} tickLine={false} axisLine={false} minTickGap={20} />
                  <YAxis tick={{ fill: T.textMuted, fontSize: 9, fontFamily: "'Share Tech Mono', monospace" }} tickLine={false} axisLine={false} tickFormatter={v => `₹${(v / 1000).toFixed(0)}K`} width={48} />
                  <Tooltip {...tooltipStyle(T)} formatter={v => [`₹${Number(v).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`, "Equity"]} />
                  <Line type="monotone" dataKey="equity" stroke={T.accent} dot={false} strokeWidth={2} style={{ filter: `drop-shadow(0 0 4px ${T.accent})` }} />
                </LineChart>
              </ResponsiveContainer>
              <div style={{ background: T.bg, border: `1px solid ${T.border}`, borderRadius: 3, padding: "10px 14px", maxHeight: 180, overflowY: "auto", marginTop: 14 }}>
                <div style={{ fontSize: 8.5, color: T.textMuted, letterSpacing: 1.5, textTransform: "uppercase", marginBottom: 8, fontFamily: "'Share Tech Mono', monospace" }}>Trade log (latest first)</div>
                {(simState.data.trades || []).length === 0 ? (
                  <div style={{ fontSize: 10, color: T.textMuted, fontFamily: "'Share Tech Mono', monospace" }}>No trades in selected period.</div>
                ) : simState.data.trades.map((t, i) => (
                  <div key={i} style={{ fontSize: 10, color: T.textSub, padding: "4px 0", borderBottom: `1px solid ${T.border}30` }}>
                    <span style={{ color: t.action === "BUY" ? T.green : T.red, fontWeight: 700, marginRight: 8, fontFamily: "'Share Tech Mono', monospace" }}>{t.action}</span>
                    <Mono size={10} color={T.textMuted}>{t.timestamp?.slice(0, 10)}</Mono>
                    <span style={{ marginLeft: 8 }}>{t.symbol} @ ₹{Math.round(t.price).toLocaleString("en-IN")}</span>
                  </div>
                ))}
              </div>
            </>
          )}

          {!simState.loading && !simState.error && !simState.data && (
            <div style={{ fontSize: 11, color: T.textMuted, fontFamily: "'Share Tech Mono', monospace", padding: "12px 0" }}>
              No backtest data yet. Click "Backfill &amp; Run" to fetch historical candles and run the AI pipeline.
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// ─── MAIN DASHBOARD ───────────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════════

export default function TradingDashboard() {
  const [isDark, setIsDark] = useState(true);
  const T = isDark ? DARK : LIGHT;
  const { liveData, connected } = useWebSocket();
  const { isMobile, isTablet } = useBreakpoint();
  const [activeTab, setActiveTab] = useState("overview");
  const [pnlHistory, setPnlHistory] = useState([]);
  const [tickHistory, setTickHistory] = useState({});
  const [prevTicks, setPrevTicks] = useState({});
  const [eventTape, setEventTape] = useState([]);
  const [lastUpdate, setLastUpdate] = useState(new Date());
  const [engineRunning, setEngineRunning] = useState(false);
  const [startingEngine, setStartingEngine] = useState(false);
  const [uiPrimarySelection, setUiPrimarySelection] = useState("dhan");
  const [brokerPrefMessage, setBrokerPrefMessage] = useState("");
  const [savingBrokerPref, setSavingBrokerPref] = useState(false);
  const [simState, setSimState] = useState({ loading: false, error: "", data: null, runId: "", runStatus: "idle", progress: null, liveReplay: null, selectionSummary: null });
  const [simBackfilling, setSimBackfilling] = useState(false);
  const [simConfig, setSimConfig] = useState({ symbols: "RELIANCE,TCS", selection_mode: "manual", budget_cap: 1000, max_auto_symbols: 3, timeframe: "day", exchange: "NSE", start_date: "2024-01-01", end_date: "2024-12-31", initial_capital: 500000, fee_pct: 0.0003, slippage_pct: 0.0005, ai_every_n_candles: 5, confidence_threshold: 0.5 });

  const { data: ordersData, refetch: refetchOrders } = useAPI("/api/orders", 10000);
  const { data: analyticsData } = useAPI("/api/analytics/performance?days=30", 60000);
  const { data: agentData } = useAPI("/api/agent/in-memory-decisions", 5000);
  const { data: riskEvents } = useAPI("/api/risk/events?limit=20", 30000);
  const { data: dailyHistory } = useAPI("/api/analytics/daily-history?days=14", 300000);
  const { data: brokerPreferenceData, refetch: refetchBrokerPreference } = useAPI("/api/settings/broker-preference", 5000);

  useEffect(() => {
    console.info("[dashboard] API routing", {
      apiBase: API_BASE || window.location.origin,
      apiBaseSource: API_BASE_SOURCE,
      wsUrl: WS_URL,
      viteApiBase: configuredApiBase || "(unset)",
      usingRemoteApi: IS_REMOTE_API,
    });
  }, []);

  useEffect(() => {
    if (!liveData?.pnl) return;
    setLastUpdate(new Date());
    setEngineRunning(liveData.engine_running || false);
    setPnlHistory(prev => {
      const now = new Date().toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
      return [...prev, { time: now, pnl: Math.round(liveData.pnl.total || 0) }].slice(-80);
    });
  }, [liveData]);

  useEffect(() => {
    const nextTicks = liveData?.ticks;
    if (!nextTicks || !Object.keys(nextTicks).length) return;
    setPrevTicks(prev => ({ ...prev, ...nextTicks }));
    setTickHistory(prev => {
      const next = { ...prev };
      Object.entries(nextTicks).forEach(([sym, val]) => {
        const p = tickPrice(val);
        if (!p) return;
        next[sym] = [...(next[sym] || []), p].slice(-30);
      });
      return next;
    });
  }, [liveData?.ticks]);

  useEffect(() => {
    const merged = [...(agentData?.agent_events || []), ...(liveData?.agent_events || [])].filter(e => e?.timestamp && e?.message);
    const seen = new Set();
    setEventTape(merged.filter(e => { const k = `${e.timestamp}-${e.message}`; return !seen.has(k) && seen.add(k); }).slice(-25));
  }, [liveData?.agent_events, agentData?.agent_events]);

  useEffect(() => {
    if (brokerPreferenceData?.ui_primary_broker) setUiPrimarySelection(brokerPreferenceData.ui_primary_broker);
  }, [brokerPreferenceData?.ui_primary_broker]);

  useEffect(() => {
    if (liveData?.ui_primary_broker) setUiPrimarySelection(liveData.ui_primary_broker);
  }, [liveData?.ui_primary_broker]);

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
  const latestDecision = agentDecisions[agentDecisions.length - 1];
  const latestSignals = (latestDecision?.signals || []).slice(0, 5);
  const pnlColor = (pnl.total || 0) >= 0 ? T.green : T.red;
  const executionPrimaryBroker = (liveData?.primary_broker || "dhan").toUpperCase();
  const replicaBroker = (liveData?.replica_broker || "zerodha").toUpperCase();
  const replicationStatus = liveData?.replication_status || "disabled";
  const replicationEnabled = Boolean(liveData?.replication_enabled);
  const replicationError = liveData?.last_replication_error || "";
  const primaryOverrideActive = Boolean(liveData?.primary_override_active);
  const effectivePrimaryBroker = (liveData?.effective_primary_broker || liveData?.primary_broker || "dhan").toUpperCase();
  const uiPrimaryBroker = (liveData?.ui_primary_broker || liveData?.primary_broker || "dhan").toUpperCase();
  const connectedBrokers = brokerPreferenceData?.connected_brokers || [];
  const progressPct = Number(agentStatus?.progress_pct || 0);

  const handleStartEngine = async () => {
    setStartingEngine(true);
    try {
      const res = await fetch(`${API_BASE}/api/engine/start`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode: "paper" }) });
      const d = await res.json();
      if (d.status === "starting") setEngineRunning(true);
    } catch (e) { alert("Failed to start engine: " + toUiError(e, "Failed")); }
    finally { setTimeout(() => setStartingEngine(false), 3000); }
  };

  const handleStopEngine = async () => {
    try { await fetch(`${API_BASE}/api/engine/stop`, { method: "POST" }); } catch {}
  };

  const handleResetKillSwitch = async () => {
    const code = prompt("Enter admin override code:");
    if (!code) return;
    try {
      const res = await fetch(`${API_BASE}/api/risk/kill-switch/reset`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ override_code: code }) });
      const d = await res.json();
      alert(d.status === "reset" ? "Kill switch reset ✅" : "Invalid code ❌");
    } catch (e) { alert("Error: " + toUiError(e, "Failed")); }
  };

  const pollReplayRun = useCallback(async (runId) => {
    const startedAt = Date.now();
    setSimState(prev => ({ ...prev, loading: true, error: "", runId, runStatus: prev.runStatus === "idle" ? "queued" : prev.runStatus }));
    while (true) {
      const statusRes = await fetch(`${API_BASE}/api/replay/runs/${runId}`);
      const status = await statusRes.json();
      if (!statusRes.ok) throw new Error(extractErrorMessage(status?.detail) || "Status failed");
      setSimState(prev => ({ ...prev, runStatus: status.status || prev.runStatus, progress: status?.metrics?.progress || null, liveReplay: status?.metrics?.live || prev.liveReplay, selectionSummary: status?.config?.selection_summary || prev.selectionSummary }));
      if (status.status === "completed") {
        const r = await fetch(`${API_BASE}/api/replay/runs/${runId}/results`);
        const result = await r.json();
        setSimState(prev => ({ ...prev, loading: false, error: "", data: { ...result, status }, runId, runStatus: "completed", progress: status?.metrics?.progress || null, liveReplay: status?.metrics?.live || prev.liveReplay, selectionSummary: status?.config?.selection_summary || prev.selectionSummary }));
        return;
      }
      if (status.status === "failed") throw new Error(extractErrorMessage(status.error) || "Replay failed");
      if ((Date.now() - startedAt) / 1000 >= REPLAY_POLL_TIMEOUT_SECONDS) {
        setSimState(prev => ({ ...prev, loading: false, runId, runStatus: status.status || "running", error: "Replay is still running. Click Rerun to resume polling." }));
        return;
      }
      await new Promise(r => setTimeout(r, 1500));
    }
  }, []);

  const loadSimulation = useCallback(async (options = {}) => {
    setSimState(prev => ({ ...prev, loading: true, error: "", data: prev.data, liveReplay: prev.liveReplay }));
    try {
      if (simState.runId) {
        const existingRes = await fetch(`${API_BASE}/api/replay/runs/${simState.runId}`);
        if (existingRes.ok) {
          const existing = await existingRes.json();
          if (["queued", "running"].includes(existing.status)) { await pollReplayRun(simState.runId); return; }
        }
      }
      const normalizedStartDate = toIsoDateOrNull(simConfig.start_date);
      const normalizedEndDate = toIsoDateOrNull(simConfig.end_date);
      const resolvedSymbols = normalizeSymbols(options.symbols ?? (simConfig.selection_mode === "auto" ? (simState.selectionSummary?.selected_symbols || []) : simConfig.symbols));
      const payload = {
        ...simConfig,
        symbols: resolvedSymbols,
        start_date: normalizedStartDate ? `${normalizedStartDate}T00:00:00` : null,
        end_date: normalizedEndDate ? `${normalizedEndDate}T23:59:59` : null,
        ai_every_n_candles: Math.max(1, Math.round(Number(simConfig.ai_every_n_candles || 1))),
        confidence_threshold: Math.max(0.3, Math.min(0.95, Number(simConfig.confidence_threshold || 0.5))),
        budget_cap: Number(simConfig.budget_cap || 0),
        max_auto_symbols: Math.max(1, Math.min(25, Math.round(Number(simConfig.max_auto_symbols || 1)))),
      };
      const startRes = await fetch(`${API_BASE}/api/replay/runs`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      const started = await startRes.json();
      if (!startRes.ok) throw new Error(extractErrorMessage(started?.detail) || "Unable to start replay");
      setSimState(prev => ({ ...prev, selectionSummary: started.selection_summary || prev.selectionSummary }));
      await pollReplayRun(started.run_id);
    } catch (e) {
      setSimState(prev => ({ ...prev, loading: false, error: toUiError(e, "Replay failed"), data: null, liveReplay: null, runStatus: "failed" }));
    }
  }, [pollReplayRun, simConfig, simState.runId, simState.selectionSummary]);

  const backfillAndRun = useCallback(async () => {
    const normalizedStartDate = toIsoDateOrNull(simConfig.start_date);
    const normalizedEndDate = toIsoDateOrNull(simConfig.end_date);
    let symbols = normalizeSymbols(simConfig.symbols);
    setSimBackfilling(true);
    setSimState(prev => ({ ...prev, error: "", liveReplay: null, data: null, progress: null }));
    try {
      if (simConfig.selection_mode === "auto") {
        if (Number(simConfig.budget_cap || 0) <= 0) throw new Error("Enter a valid budget in rupees.");
        const selectPayload = {
          symbols: [],
          exchange: simConfig.exchange,
          timeframe: simConfig.timeframe,
          start_date: normalizedStartDate ? `${normalizedStartDate}T00:00:00` : null,
          end_date: normalizedEndDate ? `${normalizedEndDate}T23:59:59` : null,
          budget_cap: Number(simConfig.budget_cap || 0),
          max_auto_symbols: Math.max(1, Math.min(25, Math.round(Number(simConfig.max_auto_symbols || 1)))),
          fee_pct: Number(simConfig.fee_pct || 0),
          slippage_pct: Number(simConfig.slippage_pct || 0),
        };
        const selectRes = await fetch(`${API_BASE}/api/replay/select-symbols`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(selectPayload) });
        const selection = await selectRes.json();
        if (!selectRes.ok) throw new Error(extractErrorMessage(selection?.detail) || "Unable to auto-pick symbols");
        symbols = selection.selected_symbols || [];
        setSimState(prev => ({ ...prev, selectionSummary: selection }));
      }
      if (!symbols.length) throw new Error(simConfig.selection_mode === "auto" ? "No affordable symbols found." : "Add at least one symbol.");
      const payload = {
        symbols,
        exchange: simConfig.exchange,
        timeframe: simConfig.timeframe,
        start_date: normalizedStartDate ? `${normalizedStartDate}T00:00:00` : null,
        end_date: normalizedEndDate ? `${normalizedEndDate}T23:59:59` : null,
      };
      const res = await fetch(`${API_BASE}/api/historical/backfill`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      const out = await res.json();
      if (!res.ok) throw new Error(extractErrorMessage(out?.detail) || "Backfill failed");
      if (Array.isArray(out?.failures) && out.failures.length) throw new Error(`Backfill: ${extractErrorMessage(out.failures[0]?.error) || "some symbols failed"}`);
      await loadSimulation({ symbols });
    } catch (e) { setSimState(prev => ({ ...prev, error: toUiError(e, "Backfill failed"), data: null, liveReplay: null })); }
    finally { setSimBackfilling(false); }
  }, [loadSimulation, simConfig]);

  const saveUiPrimaryBroker = async () => {
    setSavingBrokerPref(true);
    setBrokerPrefMessage("");
    try {
      const res = await fetch(`${API_BASE}/api/settings/broker-preference`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ui_primary_broker: uiPrimarySelection }) });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail || "Failed");
      setBrokerPrefMessage(data?.fallback_active ? `Selected ${uiPrimarySelection.toUpperCase()} unavailable, using ${String(data?.effective_primary_broker || "").toUpperCase()}` : `UI primary set to ${uiPrimarySelection.toUpperCase()} ✅`);
      refetchBrokerPreference();
    } catch (e) { setBrokerPrefMessage(`Error: ${toUiError(e, "Failed")}`); }
    finally { setSavingBrokerPref(false); }
  };

  const TABS = [
    { id: "overview", label: "Overview", icon: Activity },
    { id: "positions", label: "Positions", icon: Target },
    { id: "options", label: "Options", icon: Layers },
    { id: "watchlist", label: "Watchlist", icon: Eye },
    { id: "ai", label: "AI Brain", icon: Cpu },
    { id: "orders", label: "Orders", icon: List },
    { id: "system", label: "System", icon: Shield },
    { id: "simulator", label: "Simulator", icon: Database },
    { id: "analytics", label: "Analytics", icon: BarChart2 },
  ];

  const gridKpi = isMobile ? "repeat(2,1fr)" : isTablet ? "repeat(3,1fr)" : "repeat(6,1fr)";
  const col2 = isMobile ? "1fr" : "1fr 1fr";
  const btnPrimary = (color) => ({
    background: `${color}12`, border: `1px solid ${color}40`, borderRadius: 3,
    padding: "7px 16px", cursor: "pointer", fontSize: 9.5, fontWeight: 700,
    color, letterSpacing: 1.5, transition: "all 0.15s", fontFamily: "'Share Tech Mono', monospace",
    textTransform: "uppercase",
  });

  return (
    <div style={{ minHeight: "100vh", background: T.bg, color: T.text, fontFamily: "'Rajdhani', sans-serif", paddingBottom: isMobile ? 68 : 0, position: "relative" }}>
      <link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Rajdhani:wght@400;500;600;700&family=Orbitron:wght@400;700;900&display=swap" rel="stylesheet" />

      <style>{`
        * { box-sizing: border-box; margin: 0; padding: 0; }
        ::-webkit-scrollbar { width: 4px; height: 4px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: ${T.border}; border-radius: 0; }
        button { font-family: inherit; }
        input, select { font-family: inherit; }
        @keyframes pulse-dot {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.6; transform: scale(0.85); }
        }
        @keyframes fadeSlideIn {
          from { opacity: 0; transform: translateY(-6px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes typing-bounce {
          0%,60%,100% { opacity: 0.2; transform: translateY(0); }
          30% { opacity: 1; transform: translateY(-4px); }
        }
        .tab-content { animation: fadeIn 0.2s ease; }
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(6px); }
          to { opacity: 1; transform: translateY(0); }
        }
        input[type="date"] { color-scheme: ${isDark ? "dark" : "light"}; }
        input:focus, select:focus { border-color: ${T.accent} !important; outline: none; }
        button:hover { opacity: 0.85; }
      `}</style>

      {/* Background grid */}
      <div style={{
        position: "fixed", inset: 0, zIndex: 0, pointerEvents: "none",
        backgroundImage: `linear-gradient(${T.gridLine} 1px, transparent 1px), linear-gradient(90deg, ${T.gridLine} 1px, transparent 1px)`,
        backgroundSize: "48px 48px",
      }} />
      {isDark && (
        <>
          <div style={{ position: "fixed", top: 0, left: 0, width: 300, height: 300, background: `radial-gradient(circle, ${T.accent}06 0%, transparent 70%)`, pointerEvents: "none", zIndex: 0 }} />
          <div style={{ position: "fixed", bottom: 0, right: 0, width: 400, height: 400, background: `radial-gradient(circle, ${T.purple}05 0%, transparent 70%)`, pointerEvents: "none", zIndex: 0 }} />
        </>
      )}

      {/* ── HEADER ── */}
      <header style={{
        background: isDark ? `${T.card}f0` : `${T.card}f8`, borderBottom: `1px solid ${T.border}`,
        position: "sticky", top: 0, zIndex: 100, backdropFilter: "blur(20px)", WebkitBackdropFilter: "blur(20px)",
      }}>
        <div style={{ height: 2, background: `linear-gradient(90deg, transparent, ${T.accent}, ${T.purple}, ${T.blue}, transparent)` }} />
        <div style={{ maxWidth: 1800, margin: "0 auto", padding: isMobile ? "0 12px" : "0 20px" }}>
          <div style={{ height: 54, display: "flex", alignItems: "center", gap: isMobile ? 8 : 16 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
              <div style={{ position: "relative", width: 34, height: 34 }}>
                <div style={{ position: "absolute", inset: 0, borderRadius: 4, background: `linear-gradient(135deg, ${T.accent}30, ${T.blue}20)`, border: `1px solid ${T.accent}40` }} />
                <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <Zap size={14} color={T.accent} fill={T.accent} style={{ filter: `drop-shadow(0 0 6px ${T.accent})` }} />
                </div>
              </div>
              {!isMobile && (
                <div>
                  <div style={{ fontFamily: "'Orbitron', monospace", fontWeight: 700, fontSize: 13, letterSpacing: 2, color: T.text }}>AGENTTRADER</div>
                  <div style={{ fontSize: 8, color: T.textMuted, letterSpacing: 3, textTransform: "uppercase", fontFamily: "'Share Tech Mono', monospace" }}>NSE · F&O · GEMINI AI</div>
                </div>
              )}
            </div>

            {!isMobile && <div style={{ width: 1, height: 28, background: T.border }} />}

            {/* Index pills */}
            <div style={{ display: "flex", gap: 6, flex: 1, overflowX: "auto", scrollbarWidth: "none" }}>
              {[
                { label: "NIFTY 50", val: indices.nifty, base: 22000 },
                { label: "BANK NIFTY", val: indices.banknifty, base: 47000 },
                { label: "INDIA VIX", val: indices.vix, base: 14, warn: (indices.vix || 0) > 18 },
              ].map(idx => {
                const chg = idx.base ? (((idx.val || idx.base) - idx.base) / idx.base * 100) : 0;
                const isUp = chg >= 0;
                return (
                  <div key={idx.label} style={{ display: "flex", gap: 8, alignItems: "center", padding: "5px 12px", background: T.bg, borderRadius: 3, border: `1px solid ${T.border}`, flexShrink: 0, borderTop: `1px solid ${idx.warn ? T.amber : isUp ? T.green : T.red}40` }}>
                    <span style={{ fontSize: 8.5, color: T.textMuted, fontWeight: 700, letterSpacing: 1.5, fontFamily: "'Share Tech Mono', monospace" }}>{idx.label}</span>
                    <Mono size={12} color={idx.warn ? T.amber : T.text} weight={700}>{idx.val?.toFixed(2) || "—"}</Mono>
                    {idx.val && <span style={{ fontSize: 9.5, color: isUp ? T.green : T.red, fontFamily: "'Share Tech Mono', monospace" }}>{isUp ? "▲" : "▼"}{Math.abs(chg).toFixed(2)}%</span>}
                  </div>
                );
              })}
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
              {!isMobile && (
                <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "5px 10px", background: T.bg, borderRadius: 3, border: `1px solid ${T.border}` }}>
                  <StatusDot active={connected} color={T.green} />
                  <span style={{ fontSize: 9, color: T.textMuted, fontFamily: "'Share Tech Mono', monospace", letterSpacing: 1 }}>{connected ? "LIVE" : "OFFLINE"}</span>
                  <span style={{ color: T.textDim, fontSize: 9 }}>|</span>
                  <span style={{ fontSize: 9, color: T.textMuted, fontFamily: "'Share Tech Mono', monospace" }}>{lastUpdate.toLocaleTimeString("en-IN")}</span>
                </div>
              )}
              {killSwitch && (
                <button onClick={handleResetKillSwitch} style={{ background: T.redDim, border: `1px solid ${T.red}50`, borderRadius: 3, padding: "5px 10px", cursor: "pointer", fontSize: 9, color: T.red, fontWeight: 700, fontFamily: "'Share Tech Mono', monospace", letterSpacing: 1 }}>⚠ KILL SWITCH</button>
              )}
              <button onClick={() => setIsDark(p => !p)} style={{ background: T.bg, border: `1px solid ${T.border}`, borderRadius: 3, padding: 8, cursor: "pointer", color: T.textSub, display: "flex", alignItems: "center" }}>
                {isDark ? <Sun size={13} /> : <Moon size={13} />}
              </button>
              <button onClick={engineRunning ? handleStopEngine : handleStartEngine} disabled={startingEngine} style={{
                background: engineRunning ? `${T.green}12` : `${T.blue}12`,
                border: `1px solid ${engineRunning ? T.green : T.blue}40`, borderRadius: 3,
                padding: "6px 14px", cursor: "pointer", fontSize: 9.5, fontWeight: 700,
                letterSpacing: 1.5, color: engineRunning ? T.green : T.blue,
                display: "flex", alignItems: "center", gap: 6, whiteSpace: "nowrap", transition: "all 0.15s",
                fontFamily: "'Share Tech Mono', monospace",
              }}>
                <Power size={10} />
                {isMobile ? (startingEngine ? "…" : engineRunning ? "ON" : "GO") : (startingEngine ? "STARTING…" : engineRunning ? "ENGINE ON" : "START ENGINE")}
              </button>
            </div>
          </div>

          {!isMobile && (
            <div style={{ display: "flex", gap: 0, borderTop: `1px solid ${T.border}`, overflowX: "auto", scrollbarWidth: "none" }}>
              {TABS.map(tab => {
                const Icon = tab.icon;
                const isActive = activeTab === tab.id;
                return (
                  <button key={tab.id} onClick={() => setActiveTab(tab.id)} style={{
                    background: "none", border: "none", cursor: "pointer", padding: "9px 18px",
                    fontSize: 9.5, fontWeight: 700, letterSpacing: 1.5, textTransform: "uppercase",
                    color: isActive ? T.accent : T.textMuted,
                    borderBottom: `2px solid ${isActive ? T.accent : "transparent"}`,
                    display: "flex", alignItems: "center", gap: 6, transition: "all 0.15s", whiteSpace: "nowrap", flexShrink: 0,
                    fontFamily: "'Share Tech Mono', monospace",
                    background: isActive ? `${T.accent}04` : "none",
                  }}>
                    <Icon size={10} style={{ filter: isActive ? `drop-shadow(0 0 3px ${T.accent})` : "none" }} />
                    {tab.label}
                    {tab.id === "simulator" && <span style={{ fontSize: 7, background: T.green, color: T.bg, borderRadius: 2, padding: "1px 4px", fontWeight: 700 }}>LIVE</span>}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </header>

      <div style={{ maxWidth: 1800, margin: "0 auto", padding: isMobile ? "10px 12px 0" : "12px 20px 0", position: "relative", zIndex: 1 }}>
        <div style={{
          background: IS_REMOTE_API ? `${T.amber}10` : `${T.green}10`,
          border: `1px solid ${IS_REMOTE_API ? T.amber : T.green}45`,
          borderRadius: 4,
          padding: isMobile ? "8px 10px" : "10px 12px",
          display: "flex",
          flexWrap: "wrap",
          gap: 10,
          alignItems: "center",
          boxShadow: T.shadow,
          marginBottom: 10,
        }}>
          <span style={{ fontSize: 9, letterSpacing: 1.8, textTransform: "uppercase", color: T.textMuted, fontFamily: "'Share Tech Mono', monospace" }}>
            API Route Debug
          </span>
          <Mono size={11} color={IS_REMOTE_API ? T.amber : T.green}>
            {API_BASE || `${window.location.origin} (via proxy)`}
          </Mono>
          <span style={{ fontSize: 10, color: T.textSub }}>Source: {API_BASE_SOURCE}</span>
          <span style={{ fontSize: 10, color: T.textSub }}>WS: {WS_URL}</span>
          {isLocalHostname && !configuredApiBase && (
            <span style={{ fontSize: 10, color: T.textMuted }}>
              Local dev defaults to the Vite proxy for `http://localhost:8000`.
            </span>
          )}
          {isLocalHostname && configuredApiBase && IS_REMOTE_API && (
            <span style={{ fontSize: 10, color: T.amber }}>
              Warning: local UI is explicitly targeting a remote API via `VITE_API_BASE`.
            </span>
          )}
        </div>
      </div>
  
      {/* ── ALERTS ── */}
      <div style={{ maxWidth: 1800, margin: "0 auto", padding: isMobile ? "10px 12px 0" : "12px 20px 0", position: "relative", zIndex: 1 }}>
        {replicationEnabled && replicationStatus === "partial_failure" && (
          <div style={{ background: `${T.amber}0c`, border: `1px solid ${T.amber}30`, borderLeft: `3px solid ${T.amber}`, borderRadius: 3, padding: "9px 14px", marginBottom: 10, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <AlertTriangle size={12} color={T.amber} />
            <span style={{ fontSize: 11, color: T.amber, fontWeight: 600, fontFamily: "'Share Tech Mono', monospace" }}>REPLICA WARNING: Zerodha copy partially failing</span>
            {replicationError && <span style={{ fontSize: 10, color: T.textMuted }}>· {replicationError}</span>}
          </div>
        )}
        {!engineRunning && (
          <div style={{ background: `${T.blue}0a`, border: `1px solid ${T.blue}25`, borderLeft: `3px solid ${T.blue}`, borderRadius: 3, padding: "9px 14px", marginBottom: 10, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <Power size={12} color={T.blue} />
            <span style={{ fontSize: 11, color: T.blue, fontWeight: 600, fontFamily: "'Share Tech Mono', monospace" }}>ENGINE STOPPED</span>
            <span style={{ fontSize: 11, color: T.textMuted }}>· Live data streams when engine is running</span>
            <button onClick={handleStartEngine} style={{ ...btnPrimary(T.blue), marginLeft: "auto", padding: "5px 16px" }}>START ENGINE →</button>
          </div>
        )}
      </div>

      {/* ── CONTENT ── */}
      <main style={{ maxWidth: 1800, margin: "0 auto", padding: isMobile ? "10px 12px" : "14px 20px", position: "relative", zIndex: 1 }}>

        {/* OVERVIEW */}
        {activeTab === "overview" && (
          <div className="tab-content" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <div style={{ display: "grid", gridTemplateColumns: gridKpi, gap: 10 }}>
              <StatTile T={T} label="Today P&L"
                value={<span style={{ color: pnlColor, fontFamily: "'Share Tech Mono', monospace", fontWeight: 700, fontSize: 22 }}>{(pnl.total || 0) >= 0 ? "+" : ""}₹{Math.abs(pnl.total || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 })}</span>}
                sub={`${(pnl.pct || 0) >= 0 ? "+" : ""}${(pnl.pct || 0).toFixed(2)}% day`} color={pnlColor} icon={(pnl.total || 0) >= 0 ? TrendingUp : TrendingDown}
              />
              <StatTile T={T} label="Realized P&L"
                value={<span style={{ color: (pnl.realized || 0) >= 0 ? T.green : T.red, fontFamily: "'Share Tech Mono', monospace", fontWeight: 700, fontSize: 22 }}>{(pnl.realized || 0) >= 0 ? "+" : ""}₹{Math.abs(pnl.realized || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 })}</span>}
                sub="Booked today" color={T.green} icon={Target}
              />
              <StatTile T={T} label="Available" value={`₹${((funds.available || 0) / 1000).toFixed(1)}K`} sub={`₹${((funds.used_margin || 0) / 1000).toFixed(1)}K margin used`} color={T.blue} icon={DollarSign} />
              <StatTile T={T} label="Positions" value={positions.length} sub={`${10 - positions.length} slots free`} color={T.purple} icon={Activity} />
              <StatTile T={T} label="Win Rate" value={`${(risk.win_rate || 0).toFixed(1)}%`} sub={`${risk.trades_today || 0} trades`} color={T.amber} icon={Shield} />
              <StatTile T={T} label="Max Drawdown" value={`${(risk.drawdown_pct || 0).toFixed(2)}%`} sub={(risk.drawdown_pct || 0) < 2 ? "Within limits" : "⚠ Near limit"} color={(risk.drawdown_pct || 0) < 2 ? T.green : T.red} icon={AlertTriangle} />
            </div>

            <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "2fr 1fr", gap: 14 }}>
              <Card T={T} accent={pnlColor}>
                <CardHeader T={T} title="Intraday P&L" subtitle="Real-time equity curve" accent={pnlColor}
                  right={<span style={{ fontFamily: "'Share Tech Mono', monospace", fontSize: 14, fontWeight: 700, color: pnlColor }}>{(pnl.total || 0) >= 0 ? "+" : ""}₹{Math.abs(pnl.total || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 })}</span>}
                />
                <div style={{ padding: "14px 16px" }}>
                  <ResponsiveContainer width="100%" height={isMobile ? 130 : 170}>
                    <AreaChart data={pnlHistory} margin={{ left: 0, right: 5 }}>
                      <defs>
                        <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor={pnlColor} stopOpacity="0.3" />
                          <stop offset="100%" stopColor={pnlColor} stopOpacity="0" />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="2 2" stroke={T.border} />
                      <XAxis dataKey="time" tick={{ fill: T.textMuted, fontSize: 9, fontFamily: "'Share Tech Mono', monospace" }} tickLine={false} axisLine={false} interval={14} />
                      <YAxis tick={{ fill: T.textMuted, fontSize: 9, fontFamily: "'Share Tech Mono', monospace" }} tickLine={false} axisLine={false} tickFormatter={v => `₹${(v / 1000).toFixed(1)}K`} width={50} />
                      <Tooltip {...tooltipStyle(T)} formatter={v => [`₹${v.toLocaleString("en-IN")}`, "P&L"]} />
                      <ReferenceLine y={0} stroke={T.border} strokeDasharray="4 2" />
                      <Area type="monotone" dataKey="pnl" stroke={pnlColor} fill="url(#pnlGrad)" strokeWidth={2} dot={false} />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </Card>

              <Card T={T} accent={T.red}>
                <CardHeader T={T} title="Risk Gauges" subtitle="Limit consumption" accent={T.red} />
                <div style={{ padding: "14px 16px" }}>
                  {[
                    { label: "Daily Loss Limit", used: Math.abs(Math.min(0, risk.daily_pnl_pct || 0)), max: 2.0, color: T.red },
                    { label: "Max Drawdown", used: risk.drawdown_pct || 0, max: 8.0, color: T.amber },
                    { label: "Positions", used: positions.length, max: 10, color: T.purple, noPercent: true },
                    { label: "Margin Used", used: ((funds.used_margin || 0) / (funds.total || 1)) * 100, max: 80, color: T.blue },
                  ].map(r => {
                    const pct = Math.min((r.used / r.max) * 100, 100);
                    return (
                      <div key={r.label} style={{ marginBottom: 18 }}>
                        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                          <span style={{ fontSize: 9.5, color: T.textMuted, fontFamily: "'Share Tech Mono', monospace" }}>{r.label}</span>
                          <Mono size={10} color={pct > 75 ? T.red : T.text}>{r.noPercent ? `${Math.round(r.used)}/${r.max}` : `${r.used.toFixed(1)}%`}</Mono>
                        </div>
                        <ProgressBar value={r.used} max={r.max} color={pct > 75 ? T.red : r.color} height={4} />
                      </div>
                    );
                  })}
                </div>
              </Card>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: col2, gap: 14 }}>
              <Card T={T} accent={T.accent}>
                <CardHeader T={T} title="AI Cycle" subtitle="Current decision pipeline" accent={T.accent}
                  right={latestDecision?.market_regime ? <Pill label={latestDecision.market_regime} color={T.accent} T={T} /> : null}
                />
                <div style={{ padding: "14px 16px" }}>
                  <StagePipeline currentStage={agentStatus?.stage} T={T} />
                  <div style={{ height: 3, background: T.bg, position: "relative", overflow: "hidden", marginBottom: 8 }}>
                    <div style={{ position: "absolute", left: 0, top: 0, width: `${Math.max(2, progressPct)}%`, height: "100%", background: `linear-gradient(90deg, ${T.accent}80, ${T.accent})`, boxShadow: `0 0 8px ${T.accent}`, transition: "width 0.4s ease" }} />
                  </div>
                  <ThoughtStream thoughts={eventTape} isThinking={engineRunning && agentStatus?.stage !== "decision_complete"} T={T} />
                </div>
              </Card>

              <Card T={T} accent={T.cyan}>
                <CardHeader T={T} title="Live Ticks" subtitle="Real-time price feed" accent={T.cyan}
                  right={<StatusDot active={connected} color={T.green} />}
                />
                <div style={{ padding: "10px 16px 14px", display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 8 }}>
                  {Object.entries(ticks).slice(0, isMobile ? 6 : 9).map(([sym, val]) => {
                    const curr = tickPrice(val);
                    const prev = tickPrice(prevTicks[sym] || curr);
                    const delta = curr - prev;
                    const series = tickHistory[sym] || [];
                    const isUp = delta >= 0;
                    return (
                      <div key={sym} style={{ background: T.bg, borderRadius: 3, padding: "9px 10px", border: `1px solid ${T.border}`, borderTop: `1px solid ${isUp ? T.green : T.red}30` }}>
                        <div style={{ fontSize: 8.5, color: T.textMuted, marginBottom: 3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontFamily: "'Share Tech Mono', monospace" }}>{sym}</div>
                        <Mono size={10.5} color={isUp ? T.green : T.red} weight={700}>₹{curr ? curr.toLocaleString("en-IN") : "—"}</Mono>
                        <Sparkline data={series.slice(-15)} color={isUp ? T.green : T.red} height={24} />
                      </div>
                    );
                  })}
                  {!Object.keys(ticks).length && (
                    <div style={{ gridColumn: "1/-1", textAlign: "center", padding: "24px 0", fontSize: 11, color: T.textMuted, fontFamily: "'Share Tech Mono', monospace" }}>WAITING FOR TICK DATA…</div>
                  )}
                </div>
              </Card>
            </div>
          </div>
        )}

        {activeTab === "positions" && (
          <div className="tab-content" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <PositionsPanel positions={positions} tickHistory={tickHistory} T={T} />
            <SLTrackerPanel positions={positions} ticks={ticks} T={T} />
          </div>
        )}

        {activeTab === "options" && (
          <div className="tab-content" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <OptionsChainPanel data={liveData?.options_chain || null} T={T} />
            <IntradayChartPanel ticks={ticks} tickHistory={tickHistory} T={T} />
          </div>
        )}

        {activeTab === "watchlist" && (
          <div className="tab-content" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <WatchlistPanel data={liveData?.watchlist || null} T={T} />
          </div>
        )}

        {activeTab === "ai" && (
          <div className="tab-content" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <AITimelinePanel decisions={agentDecisions} T={T} />
            <StrategyReviewPanel reviewData={null} T={T} />
            <ModelPerformancePanel decisions={agentDecisions} T={T} />
            <Card T={T} accent={T.purple}>
              <CardHeader T={T} title="Latest AI Signals" accent={T.purple}
                subtitle={`${latestSignals.length} signals · ${latestDecision?.timestamp ? new Date(latestDecision.timestamp).toLocaleTimeString("en-IN") : "—"}`}
              />
              <div style={{ padding: "14px 16px", display: "grid", gridTemplateColumns: isMobile ? "1fr" : "repeat(auto-fill, minmax(280px, 1fr))", gap: 12 }}>
                {latestSignals.map((s, i) => <SignalCard key={i} signal={s} T={T} />)}
                {!latestSignals.length && <div style={{ fontSize: 11, color: T.textMuted, fontFamily: "'Share Tech Mono', monospace" }}>NO LIVE AI SIGNALS YET</div>}
              </div>
            </Card>
          </div>
        )}

        {activeTab === "orders" && (
          <div className="tab-content" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <ExecutionQueuePanel orders={orders} T={T} />
            <Card T={T} accent={T.blue}>
              <CardHeader T={T} title="Order History" subtitle="All orders today" accent={T.blue}
                right={<button onClick={refetchOrders} style={{ background: "none", border: "none", cursor: "pointer", color: T.textMuted, padding: 4 }}><RefreshCw size={12} /></button>}
              />
              <div style={{ padding: "0 16px 14px", overflowX: "auto" }}>
                {!orders.length ? (
                  <div style={{ textAlign: "center", padding: "40px 0", color: T.textMuted, fontSize: 12, fontFamily: "'Share Tech Mono', monospace" }}>NO ORDERS TODAY</div>
                ) : (
                  <table style={{ width: "100%", borderCollapse: "collapse", marginTop: 10, minWidth: 600 }}>
                    <thead>
                      <tr style={{ borderBottom: `1px solid ${T.border}` }}>
                        {["Time", "Symbol", "Side", "Qty", "Price", "Avg Fill", "Status", "Slippage"].map(h =>
                          <th key={h} style={{ padding: "7px 8px", textAlign: "left", fontSize: 8.5, color: T.textMuted, fontWeight: 700, letterSpacing: 1.5, textTransform: "uppercase", fontFamily: "'Share Tech Mono', monospace" }}>{h}</th>
                        )}
                      </tr>
                    </thead>
                    <tbody>
                      {orders.slice(0, 30).map((o, i) => {
                        const slip = o.price && o.average_price ? ((o.average_price - o.price) / o.price * 100) : null;
                        const statusColor = { COMPLETE: T.green, FILLED: T.green, PENDING: T.amber, REJECTED: T.red }[(o.status || "").toUpperCase()] || T.textMuted;
                        return (
                          <tr key={i} style={{ borderBottom: `1px solid ${T.border}15`, background: i % 2 ? T.bg + "50" : "transparent" }}>
                            <td style={{ padding: "9px 8px" }}><Mono size={10} color={T.textMuted}>{new Date(o.placed_at).toLocaleTimeString("en-IN")}</Mono></td>
                            <td style={{ padding: "9px 8px", fontWeight: 700, fontSize: 11, color: T.text, fontFamily: "'Share Tech Mono', monospace" }}>{o.symbol}</td>
                            <td style={{ padding: "9px 8px" }}><Pill label={o.side} color={o.side === "BUY" ? T.green : T.red} T={T} /></td>
                            <td style={{ padding: "9px 8px" }}><Mono size={11} color={T.text}>{o.quantity}</Mono></td>
                            <td style={{ padding: "9px 8px" }}><Mono size={11} color={T.text}>{o.price ? `₹${o.price}` : "MKT"}</Mono></td>
                            <td style={{ padding: "9px 8px" }}><Mono size={11} color={T.textSub}>{o.average_price ? `₹${o.average_price}` : "—"}</Mono></td>
                            <td style={{ padding: "9px 8px" }}><Pill label={o.status} color={statusColor} T={T} /></td>
                            <td style={{ padding: "9px 8px" }}><Mono size={10} color={slip !== null && Math.abs(slip) > 0.1 ? T.amber : T.textMuted}>{slip !== null ? `${slip.toFixed(3)}%` : "—"}</Mono></td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
              </div>
            </Card>
          </div>
        )}

        {activeTab === "system" && (
          <div className="tab-content" style={{ display: "grid", gridTemplateColumns: col2, gap: 14 }}>
            <KillSwitchPanel risk={risk} riskEvents={riskEvents} T={T} onReset={handleResetKillSwitch} />
            <ModelPerformancePanel decisions={agentDecisions} T={T} />
            <Card T={T} accent={T.blue}>
              <CardHeader T={T} title="UI Primary Broker" subtitle="Dashboard data source" accent={T.blue} />
              <div style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 12 }}>
                <div style={{ display: "flex", gap: 8 }}>
                  {["dhan", "zerodha"].map(b => (
                    <button key={b} onClick={() => setUiPrimarySelection(b)} style={{
                      background: uiPrimarySelection === b ? T.accentDim : "transparent",
                      border: `1px solid ${uiPrimarySelection === b ? T.accent : T.border}`,
                      color: uiPrimarySelection === b ? T.accent : T.textMuted,
                      borderRadius: 3, padding: "7px 18px", fontSize: 9.5, fontWeight: 700, cursor: "pointer",
                      textTransform: "uppercase", fontFamily: "'Share Tech Mono', monospace", letterSpacing: 1,
                    }}>{b}</button>
                  ))}
                </div>
                {primaryOverrideActive && <div style={{ fontSize: 10, color: T.amber, fontFamily: "'Share Tech Mono', monospace" }}>⚠ Fallback active. {liveData?.primary_override_reason || ""}</div>}
                {brokerPrefMessage && <div style={{ fontSize: 10, color: brokerPrefMessage.startsWith("Error") ? T.red : T.green, fontFamily: "'Share Tech Mono', monospace" }}>{brokerPrefMessage}</div>}
                <button onClick={saveUiPrimaryBroker} disabled={savingBrokerPref} style={btnPrimary(T.blue)}>{savingBrokerPref ? "SAVING…" : "SAVE PREFERENCE"}</button>
              </div>
            </Card>
            <Card T={T} accent={T.green}>
              <CardHeader T={T} title="Broker Health" subtitle="Connectivity status" accent={T.green} />
              <div style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
                {["dhan", "zerodha"].map(b => {
                  const isConn = connectedBrokers.includes(b);
                  return (
                    <div key={b} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "12px 16px", background: T.bg, borderRadius: 3, border: `1px solid ${isConn ? T.green : T.red}20`, borderLeft: `2px solid ${isConn ? T.green : T.red}` }}>
                      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                        <StatusDot active={isConn} color={isConn ? T.green : T.red} />
                        <span style={{ fontSize: 13, fontWeight: 700, color: T.text, textTransform: "uppercase", fontFamily: "'Share Tech Mono', monospace" }}>{b}</span>
                      </div>
                      <Pill label={isConn ? "healthy" : "down"} color={isConn ? T.green : T.red} T={T} />
                    </div>
                  );
                })}
              </div>
            </Card>
            <Card T={T} accent={T.textMuted} style={{ gridColumn: isMobile ? "1" : "1/-1" }}>
              <CardHeader T={T} title="Event Log" subtitle="Full AI agent pipeline events" accent={T.textMuted} />
              <div style={{ padding: "10px 16px 14px", maxHeight: 320, overflowY: "auto" }}>
                {!eventTape.length ? (
                  <div style={{ fontSize: 11, color: T.textMuted, padding: "20px 0", fontFamily: "'Share Tech Mono', monospace" }}>No events yet</div>
                ) : eventTape.slice().reverse().map((e, i) => (
                  <div key={i} style={{ display: "flex", gap: 8, padding: "6px 8px", borderRadius: 2, marginBottom: 2, background: i % 2 === 0 ? T.bg : "transparent", alignItems: "flex-start", borderLeft: `1px solid ${e.level === "error" ? T.red : e.level === "success" ? T.green : T.amber}30` }}>
                    <Mono size={9} color={T.textDim}>{new Date(e.timestamp).toLocaleTimeString("en-IN")}</Mono>
                    <span style={{ width: 4, height: 4, borderRadius: "50%", background: e.level === "error" ? T.red : e.level === "success" ? T.green : T.amber, marginTop: 5, flexShrink: 0 }} />
                    <span style={{ fontSize: 10.5, color: e.level === "error" ? T.red : e.level === "success" ? T.green : T.textSub, flex: 1 }}>{e.message}</span>
                    {e.stage && <Mono size={9} color={T.textDim}>{e.stage}</Mono>}
                  </div>
                ))}
              </div>
            </Card>
          </div>
        )}

        {/* ── SIMULATOR TAB (FULL) ── */}
        {activeTab === "simulator" && (
          <div className="tab-content">
            <SimulatorTab
              T={T}
              simState={simState}
              simConfig={simConfig}
              setSimConfig={setSimConfig}
              loadSimulation={loadSimulation}
              backfillAndRun={backfillAndRun}
              simBackfilling={simBackfilling}
            />
          </div>
        )}

        {activeTab === "analytics" && (
          <div className="tab-content" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <div style={{ display: "grid", gridTemplateColumns: isMobile ? "repeat(2,1fr)" : isTablet ? "repeat(3,1fr)" : "repeat(6,1fr)", gap: 10 }}>
              {[
                { label: "30D P&L", value: `₹${((analyticsData?.total_pnl || 0) / 1000).toFixed(1)}K`, color: T.accent },
                { label: "Total Trades", value: analyticsData?.total_trades || 0, color: T.blue },
                { label: "Win Rate", value: `${(analyticsData?.win_rate || 0).toFixed(1)}%`, color: T.green },
                { label: "Avg Win", value: `₹${(analyticsData?.avg_win || 0).toFixed(0)}`, color: T.green },
                { label: "Avg Loss", value: `₹${(analyticsData?.avg_loss || 0).toFixed(0)}`, color: T.red },
                { label: "Profit Factor", value: (analyticsData?.profit_factor || 0).toFixed(2), color: T.amber },
              ].map(s => <StatTile key={s.label} T={T} label={s.label} value={s.value} color={s.color} />)}
            </div>
            <Card T={T} accent={T.accent}>
              <CardHeader T={T} title="14-Day P&L History" subtitle="Net daily returns" accent={T.accent} />
              <div style={{ padding: "14px 16px" }}>
                {dailyHistory?.history?.length ? (
                  <ResponsiveContainer width="100%" height={200}>
                    <BarChart data={dailyHistory.history.slice().reverse()} margin={{ left: 0 }}>
                      <CartesianGrid strokeDasharray="2 2" stroke={T.border} />
                      <XAxis dataKey="date" tick={{ fill: T.textMuted, fontSize: 9, fontFamily: "'Share Tech Mono', monospace" }} tickLine={false} axisLine={false} />
                      <YAxis tick={{ fill: T.textMuted, fontSize: 9, fontFamily: "'Share Tech Mono', monospace" }} tickLine={false} axisLine={false} tickFormatter={v => `₹${(v / 1000).toFixed(1)}K`} width={50} />
                      <Tooltip {...tooltipStyle(T)} formatter={v => [`₹${v.toLocaleString("en-IN")}`, "Net P&L"]} />
                      <ReferenceLine y={0} stroke={T.border} />
                      <Bar dataKey="net_pnl" radius={[2, 2, 0, 0]}>
                        {(dailyHistory.history || []).map((entry, i) => <Cell key={i} fill={(entry.net_pnl || 0) >= 0 ? T.green : T.red} opacity={0.8} />)}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                ) : (
                  <div style={{ textAlign: "center", padding: "40px", color: T.textMuted, fontSize: 12, fontFamily: "'Share Tech Mono', monospace" }}>NO HISTORY YET</div>
                )}
              </div>
            </Card>
            <AITimelinePanel decisions={agentDecisions} T={T} />
          </div>
        )}
      </main>

      {isMobile && <BottomNav tabs={TABS} active={activeTab} onChange={setActiveTab} T={T} />}
    </div>
  );
}  

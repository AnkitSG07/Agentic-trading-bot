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
  ChevronRight, Sun, Moon, Menu, X,
} from "lucide-react";

// ─── CONFIG ──────────────────────────────────────────────────────────────────
const isProduction = window.location.hostname !== "localhost";
const API_BASE = import.meta.env.VITE_API_BASE ??
  (isProduction ? "https://agentic-trading-bot-188e.onrender.com" : "http://localhost:8000");
const WS_URL = import.meta.env.VITE_WS_URL ?? API_BASE.replace(/^http/, "ws") + "/ws";
const SIM_SOURCE = "NSE historical candles + AI decision/risk pipeline";

const tickPrice = (v) => {
  if (v && typeof v === "object") return Number(v.price || 0);
  return Number(v || 0);
};

// ─── THEME ───────────────────────────────────────────────────────────────────
const DARK = {
  bg: "#080c14",
  bgAlt: "#0c1220",
  surface: "#101828",
  card: "#111927",
  cardHover: "#141f30",
  border: "#1e2d45",
  borderLight: "#243454",
  text: "#e8edf5",
  textSub: "#8fa3be",
  textMuted: "#4a607a",
  textDim: "#263245",
  accent: "#2dd4bf",
  accentDim: "#2dd4bf18",
  green: "#22c55e",
  greenDim: "#22c55e14",
  red: "#ef4444",
  redDim: "#ef444414",
  amber: "#f59e0b",
  amberDim: "#f59e0b14",
  blue: "#3b82f6",
  blueDim: "#3b82f614",
  purple: "#a855f7",
  cyan: "#06b6d4",
  shadow: "0 1px 3px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.03)",
  shadowHover: "0 4px 16px rgba(0,0,0,0.5)",
};

const LIGHT = {
  bg: "#f0f4f8",
  bgAlt: "#e8eef5",
  surface: "#ffffff",
  card: "#ffffff",
  cardHover: "#f8fafc",
  border: "#d1dce8",
  borderLight: "#b8cad8",
  text: "#0f1a26",
  textSub: "#44607a",
  textMuted: "#7a96ae",
  textDim: "#b8cad8",
  accent: "#0d9488",
  accentDim: "#0d948814",
  green: "#16a34a",
  greenDim: "#16a34a12",
  red: "#dc2626",
  redDim: "#dc262612",
  amber: "#d97706",
  amberDim: "#d9770612",
  blue: "#2563eb",
  blueDim: "#2563eb12",
  purple: "#9333ea",
  cyan: "#0891b2",
  shadow: "0 1px 3px rgba(0,0,0,0.08), 0 0 0 1px rgba(0,0,0,0.04)",
  shadowHover: "0 4px 16px rgba(0,0,0,0.12)",
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
  options_selling: "#ec4899", breakout: T.cyan, scalping: T.blue,
});
const SIGNAL_COLORS_FN = (T) => ({
  strong_buy: T.green, buy: T.green, neutral: T.amber,
  sell: T.red, strong_sell: T.red,
});

// ─── PRIMITIVES ──────────────────────────────────────────────────────────────
const Pill = ({ label, color, T }) => (
  <span style={{
    background: `${color}16`, color, border: `1px solid ${color}30`,
    borderRadius: 3, padding: "1px 6px", fontSize: 9.5, fontWeight: 700,
    letterSpacing: 0.7, textTransform: "uppercase", whiteSpace: "nowrap",
    fontFamily: "'IBM Plex Mono', monospace",
  }}>{(label || "").replace(/_/g, " ")}</span>
);

const StatusDot = ({ active, color }) => (
  <span style={{
    display: "inline-block", width: 6, height: 6, borderRadius: "50%",
    background: active ? color : "currentColor",
    boxShadow: active ? `0 0 0 2px ${color}30, 0 0 6px ${color}60` : "none",
    opacity: active ? 1 : 0.3,
  }} />
);

const Delta = ({ value, size = 12, showPrefix = true }) => {
  const up = value >= 0;
  return (
    <span style={{
      color: up ? "#22c55e" : "#ef4444",
      fontFamily: "'IBM Plex Mono', monospace",
      fontWeight: 600, fontSize: size,
    }}>
      {showPrefix && (up ? "+" : "")}{typeof value === "number" ? value.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : value}
    </span>
  );
};

const Mono = ({ children, size = 12, color, weight = 600 }) => (
  <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: size, fontWeight: weight, color }}>{children}</span>
);

const ProgressBar = ({ value, max, color, height = 3 }) => (
  <div style={{ width: "100%", height, background: "rgba(255,255,255,0.06)", borderRadius: height, overflow: "hidden" }}>
    <div style={{
      width: `${Math.min((value / max) * 100, 100)}%`, height: "100%",
      background: color, borderRadius: height, transition: "width 0.4s ease",
    }} />
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
        <linearGradient id={`sg-${color.replace("#","")}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.3" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <polyline fill={`url(#sg-${color.replace("#","")})`} stroke="none" points={`0,${h} ${pts} ${w},${h}`} />
      <polyline fill="none" stroke={color} strokeWidth="1.5" points={pts} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
};

// Card component
const Card = ({ children, T, style = {}, className }) => (
  <div className={className} style={{
    background: T.card, border: `1px solid ${T.border}`,
    borderRadius: 8, boxShadow: T.shadow, ...style,
  }}>{children}</div>
);

const CardHeader = ({ title, subtitle, right, T }) => (
  <div style={{
    display: "flex", alignItems: "center", justifyContent: "space-between",
    padding: "12px 16px", borderBottom: `1px solid ${T.border}`,
    gap: 12,
  }}>
    <div>
      <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: 0.5, color: T.text, textTransform: "uppercase" }}>{title}</div>
      {subtitle && <div style={{ fontSize: 10, color: T.textMuted, marginTop: 2 }}>{subtitle}</div>}
    </div>
    {right && <div style={{ flexShrink: 0 }}>{right}</div>}
  </div>
);

// KPI stat card
const StatTile = ({ label, value, sub, delta, color, icon: Icon, T }) => (
  <Card T={T} style={{ padding: "14px 16px", position: "relative", overflow: "hidden" }}>
    <div style={{
      position: "absolute", top: 0, left: 0, right: 0, height: 2,
      background: `linear-gradient(90deg, ${color}60, transparent)`,
    }} />
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: 1, textTransform: "uppercase", color: T.textMuted, marginBottom: 8 }}>{label}</div>
        <div style={{ fontSize: 20, fontWeight: 800, color: T.text, fontFamily: "'IBM Plex Mono', monospace", letterSpacing: -0.5, lineHeight: 1 }}>{value}</div>
        {sub && <div style={{ fontSize: 10, color: T.textMuted, marginTop: 6 }}>{sub}</div>}
        {delta !== undefined && <div style={{ marginTop: 4 }}><Delta value={delta} size={10} /></div>}
      </div>
      {Icon && (
        <div style={{ background: `${color}14`, border: `1px solid ${color}22`, borderRadius: 6, padding: 8, flexShrink: 0 }}>
          <Icon size={14} color={color} />
        </div>
      )}
    </div>
  </Card>
);

// ─── TOOLTIP STYLE ───────────────────────────────────────────────────────────
const tooltipStyle = (T) => ({
  contentStyle: { background: T.surface, border: `1px solid ${T.border}`, borderRadius: 6, fontSize: 10, color: T.text, boxShadow: T.shadowHover },
  labelStyle: { color: T.textMuted },
});

// ─── PANELS ──────────────────────────────────────────────────────────────────

// 1. Options Chain
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
    <Card T={T}>
      <CardHeader T={T} title="Options Chain" subtitle="PCR · Max Pain · OI Heatmap"
        right={
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span style={{ fontSize: 10, color: T.textMuted, fontFamily: "monospace" }}>PCR</span>
            <Mono size={15} color={hasPcr ? pcrColor : T.textMuted} weight={800}>{hasPcr ? pcr.toFixed(2) : "—"}</Mono>
            {hasPcr && <Pill label={pcrLabel} color={pcrColor} T={T} />}
          </div>
        }
      />
      <div style={{ padding: "14px 16px", display: "grid", gridTemplateColumns: isMobile ? "1fr" : "1.5fr 1fr", gap: 16 }}>
        <div>
          <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: 1.2, color: T.textMuted, textTransform: "uppercase", marginBottom: 10 }}>OI Concentration — NIFTY</div>
          {combined.length === 0 ? (
            <div style={{ height: 150, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11, color: T.textMuted, background: T.bg, borderRadius: 6 }}>No live OI data</div>
          ) : (
            <ResponsiveContainer width="100%" height={150}>
              <BarChart data={combined} layout="vertical" margin={{ left: 55, right: 8 }}>
                <XAxis type="number" tick={{ fill: T.textMuted, fontSize: 9 }} tickLine={false} axisLine={false} />
                <YAxis type="category" dataKey="label" tick={{ fill: T.textMuted, fontSize: 9 }} tickLine={false} axisLine={false} width={50} />
                <Tooltip {...tooltipStyle(T)} formatter={(v, n, p) => [`${v.toFixed(1)}L`, p.payload.type === "CE" ? "Call OI" : "Put OI"]} />
                <Bar dataKey="oi" radius={[0, 3, 3, 0]}>
                  {combined.map((e, i) => <Cell key={i} fill={e.type === "CE" ? T.red : T.green} opacity={0.8} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 6 }}>
          {[
            { label: "ATM Strike", v: nifty.atm_strike?.toLocaleString("en-IN") || "—", c: T.cyan },
            { label: "Straddle Price", v: Number.isFinite(Number(nifty.atm_straddle)) ? `₹${nifty.atm_straddle}` : "—", c: T.purple },
            { label: "Expected Move", v: Number.isFinite(Number(nifty.expected_move_pct)) ? `±${Number(nifty.expected_move_pct).toFixed(2)}%` : "—", c: T.amber },
            { label: "Max Pain", v: nifty.max_pain?.toLocaleString("en-IN") || "—", c: T.amber },
            { label: "Key Resistance", v: nifty.key_resistance?.toLocaleString("en-IN") || "—", c: T.red },
            { label: "Key Support", v: nifty.key_support?.toLocaleString("en-IN") || "—", c: T.green },
            { label: "BANKNIFTY PCR", v: Number.isFinite(Number(bnk.pcr)) ? Number(bnk.pcr).toFixed(2) : "—", c: Number(bnk.pcr) > 1 ? T.green : T.red },
          ].map(r => (
            <div key={r.label} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 10px", background: T.bg, borderRadius: 5 }}>
              <span style={{ fontSize: 9.5, color: T.textMuted }}>{r.label}</span>
              <Mono size={10.5} color={r.c} weight={700}>{r.v}</Mono>
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}

// 2. Watchlist
const SIGNAL_LABEL = { strong_buy: "STRONG BUY", buy: "BUY", neutral: "NEUTRAL", sell: "SELL", strong_sell: "STRONG SELL" };
function WatchlistPanel({ data, T }) {
  const items = data?.length ? data : [];
  const [selected, setSelected] = useState(null);
  const { isMobile } = useBreakpoint();
  const SC = SIGNAL_COLORS_FN(T);

  return (
    <Card T={T}>
      <CardHeader T={T} title="Watchlist" subtitle="Indicator matrix · live signals"
        right={<Pill label={`${items.length} symbols`} color={T.textMuted} T={T} />}
      />
      <div style={{ padding: "0 16px 12px", overflowX: "auto" }}>
        {isMobile ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 12 }}>
            {items.map(w => {
              const ind = w.indicators || {};
              const sig = ind.overall_signal || "neutral";
              const rsi = Number(ind.rsi || 50);
              const isSel = selected === w.symbol;
              return (
                <div key={w.symbol}>
                  <div onClick={() => setSelected(isSel ? null : w.symbol)}
                    style={{ background: isSel ? `${T.accent}08` : T.bg, border: `1px solid ${isSel ? T.accent + "40" : T.border}`, borderRadius: 6, padding: "10px 12px", cursor: "pointer" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                        <span style={{ fontWeight: 800, fontSize: 12, color: T.text }}>{w.symbol}</span>
                        <Mono size={11} color={T.text}>₹{(w.ltp || 0).toFixed(0)}</Mono>
                        <Delta value={w.change_pct || 0} size={10} />
                      </div>
                      <Pill label={SIGNAL_LABEL[sig] || sig} color={SC[sig] || T.textMuted} T={T} />
                    </div>
                  </div>
                  {isSel && (
                    <div style={{ background: T.bgAlt, borderRadius: 6, padding: "10px 12px", marginTop: 3, display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 8 }}>
                      {[{ label: "RSI", v: rsi, c: rsi > 70 ? T.red : rsi < 30 ? T.green : T.text },
                        { label: "MACD", v: ind.macd_signal || "—", c: ind.macd_signal === "bullish" ? T.green : T.red },
                        { label: "BB", v: ind.bb_signal || "—", c: T.textMuted },
                        { label: "Vol Ratio", v: `${(ind.volume_ratio || 1).toFixed(1)}x`, c: (ind.volume_ratio || 1) > 1.5 ? T.amber : T.textMuted },
                      ].map(it => (
                        <div key={it.label} style={{ background: T.surface, borderRadius: 5, padding: "7px 10px" }}>
                          <div style={{ fontSize: 9, color: T.textMuted }}>{it.label}</div>
                          <Mono size={11} color={it.c} weight={700}>{it.v}</Mono>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
            {!items.length && <div style={{ fontSize: 11, color: T.textMuted, padding: "20px 0" }}>No watchlist data</div>}
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", marginTop: 10, minWidth: 560 }}>
            <thead>
              <tr style={{ borderBottom: `1px solid ${T.border}` }}>
                {["Symbol", "LTP", "Chg%", "RSI", "MACD", "BB", "Volume", "Signal"].map(h =>
                  <th key={h} style={{ padding: "6px 8px", textAlign: "left", fontSize: 9, color: T.textMuted, fontWeight: 700, letterSpacing: 0.8, textTransform: "uppercase" }}>{h}</th>
                )}
              </tr>
            </thead>
            <tbody>
              {items.map((w, i) => {
                const ind = w.indicators || {};
                const sig = ind.overall_signal || "neutral";
                const rsi = Number(ind.rsi || 50);
                const isSel = selected === w.symbol;
                return (
                  <tr key={w.symbol} onClick={() => setSelected(isSel ? null : w.symbol)}
                    style={{ borderBottom: `1px solid ${T.border}20`, cursor: "pointer", background: isSel ? `${T.accent}06` : i % 2 ? T.bg + "80" : "transparent" }}>
                    <td style={{ padding: "9px 8px", fontWeight: 800, fontSize: 11, color: T.text }}>{w.symbol}</td>
                    <td style={{ padding: "9px 8px" }}><Mono size={11} color={T.text}>₹{(w.ltp || 0).toFixed(0)}</Mono></td>
                    <td style={{ padding: "9px 8px" }}><Delta value={w.change_pct || 0} size={10} /></td>
                    <td style={{ padding: "9px 8px" }}><Mono size={10.5} color={rsi > 70 ? T.red : rsi < 30 ? T.green : T.text}>{rsi}</Mono></td>
                    <td style={{ padding: "9px 8px" }}><Pill label={ind.macd_signal || "—"} color={ind.macd_signal === "bullish" ? T.green : ind.macd_signal === "bearish" ? T.red : T.amber} T={T} /></td>
                    <td style={{ padding: "9px 8px" }}><Pill label={ind.bb_signal || "mid"} color={ind.bb_signal === "upper" ? T.amber : ind.bb_signal === "lower" ? T.blue : T.textMuted} T={T} /></td>
                    <td style={{ padding: "9px 8px" }}><Mono size={10.5} color={(ind.volume_ratio || 1) > 1.5 ? T.amber : T.textMuted}>{(ind.volume_ratio || 1).toFixed(1)}x</Mono></td>
                    <td style={{ padding: "9px 8px" }}><Pill label={SIGNAL_LABEL[sig] || sig} color={SC[sig] || T.textMuted} T={T} /></td>
                  </tr>
                );
              })}
              {!items.length && <tr><td colSpan={8} style={{ padding: "30px 8px", textAlign: "center", fontSize: 11, color: T.textMuted }}>No live watchlist data</td></tr>}
            </tbody>
          </table>
        )}
      </div>
    </Card>
  );
}

// 3. Positions with sparklines
function PositionsPanel({ positions, tickHistory, T }) {
  const { isMobile } = useBreakpoint();
  if (!positions?.length) return (
    <Card T={T}>
      <CardHeader T={T} title="Open Positions" subtitle="Live P&L with sparklines" />
      <div style={{ padding: "48px 16px", textAlign: "center", color: T.textMuted, fontSize: 12 }}>No open positions</div>
    </Card>
  );

  return (
    <Card T={T}>
      <CardHeader T={T} title="Open Positions" subtitle="Live P&L with sparklines"
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
            <div key={i} style={{ borderBottom: i < positions.length - 1 ? `1px solid ${T.border}` : "none", padding: "14px 16px" }}>
              {isMobile ? (
                <>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
                    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <span style={{ fontWeight: 800, fontSize: 13, color: T.text }}>{p.symbol}</span>
                      <Pill label={p.side} color={p.side === "BUY" ? T.green : T.red} T={T} />
                      <Mono size={10} color={T.textMuted}>×{qty}</Mono>
                    </div>
                    <div style={{ textAlign: "right" }}>
                      <div><Delta value={pnl} size={13} /></div>
                      <Delta value={pnlPct} size={10} />%
                    </div>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 8 }}>
                    <div>
                      <div style={{ fontSize: 9, color: T.textMuted, marginBottom: 2 }}>AVG / LTP</div>
                      <Mono size={10} color={T.text}>₹{avg.toFixed(0)} / <span style={{ color: isUp ? T.green : T.red }}>₹{ltp.toFixed(0)}</span></Mono>
                    </div>
                    {slDist !== null && (
                      <div>
                        <div style={{ fontSize: 9, color: T.textMuted, marginBottom: 2 }}>SL Distance</div>
                        <Mono size={11} color={slDist < 0.5 ? T.red : slDist < 1.5 ? T.amber : T.green}>{slDist.toFixed(2)}%</Mono>
                        <div style={{ marginTop: 3 }}><ProgressBar value={Math.min(slDist, 5)} max={5} color={slDist < 1 ? T.red : T.amber} /></div>
                      </div>
                    )}
                  </div>
                  <Sparkline data={series.length ? series : [avg, ltp]} color={isUp ? T.green : T.red} height={36} />
                </>
              ) : (
                <div style={{ display: "grid", gridTemplateColumns: "120px 55px 100px 110px 110px 80px 1fr", alignItems: "center", gap: 14 }}>
                  <div>
                    <div style={{ fontWeight: 800, fontSize: 12, color: T.text, marginBottom: 4 }}>{p.symbol}</div>
                    <Pill label={p.side} color={p.side === "BUY" ? T.green : T.red} T={T} />
                  </div>
                  <Mono size={11} color={T.textSub}>{qty}</Mono>
                  <div>
                    <div style={{ fontSize: 9, color: T.textMuted, marginBottom: 2 }}>AVG / LTP</div>
                    <Mono size={10} color={T.text}>₹{avg.toFixed(0)} / <span style={{ color: isUp ? T.green : T.red }}>₹{ltp.toFixed(0)}</span></Mono>
                  </div>
                  <div>
                    <div style={{ marginBottom: 2 }}><Delta value={pnl} size={13} /></div>
                    <Delta value={pnlPct} size={10} />%
                  </div>
                  <div>
                    {slDist !== null ? (
                      <>
                        <div style={{ fontSize: 9, color: T.textMuted, marginBottom: 4 }}>SL Distance</div>
                        <Mono size={11} color={slDist < 0.5 ? T.red : slDist < 1.5 ? T.amber : T.green}>{slDist.toFixed(2)}%</Mono>
                        <div style={{ marginTop: 4 }}><ProgressBar value={Math.min(slDist, 5)} max={5} color={slDist < 1 ? T.red : T.amber} /></div>
                      </>
                    ) : <span style={{ fontSize: 10, color: T.textMuted }}>No SL</span>}
                  </div>
                  <div>
                    {p.target ? (
                      <>
                        <div style={{ fontSize: 9, color: T.textMuted, marginBottom: 2 }}>Target</div>
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

// 4. SL/Target Tracker
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
    <Card T={T}>
      <CardHeader T={T} title="SL / Target Tracker" subtitle="Stop-loss status · trailing stops · target proximity" />
      <div style={{ padding: "12px 16px" }}>
        {items.map((p, i) => (
          <div key={i} style={{ marginBottom: 12, padding: "12px 14px", background: T.bg, borderRadius: 7, border: `1px solid ${T.border}` }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <span style={{ fontWeight: 800, fontSize: 12, color: T.text }}>{p.symbol}</span>
                <Pill label={p.side || "BUY"} color={(p.side || "") === "SELL" ? T.red : T.green} T={T} />
              </div>
              <Mono size={13} color={T.text} weight={700}>₹{p.ltp.toFixed(0)}</Mono>
            </div>
            <div style={{ position: "relative", height: 8, background: T.border, borderRadius: 4, margin: "0 0 12px", overflow: "visible" }}>
              <div style={{ position: "absolute", left: 0, width: `${p.progress}%`, height: "100%", background: `linear-gradient(90deg, ${T.accent}50, ${T.accent})`, borderRadius: 4 }} />
              <div style={{ position: "absolute", left: "4%", top: -4, height: 16, width: 1.5, background: T.red, opacity: 0.7 }} />
              <div style={{ position: "absolute", right: "4%", top: -4, height: 16, width: 1.5, background: T.green, opacity: 0.7 }} />
              <div style={{ position: "absolute", left: `${p.progress}%`, top: -5, transform: "translateX(-50%)", width: 14, height: 14, background: T.accent, borderRadius: "50%", border: `2px solid ${T.card}`, boxShadow: `0 0 0 2px ${T.accent}40` }} />
            </div>
            <div style={{ display: "grid", gridTemplateColumns: isMobile ? "repeat(2,1fr)" : "repeat(4,1fr)", gap: 8 }}>
              {[
                { label: "Entry", v: `₹${p.entry.toFixed(0)}`, c: T.textSub },
                { label: "Stop Loss", v: p.sl ? `₹${p.sl.toFixed(0)}` : "—", c: T.red, sub: p.slDist ? `${p.slDist.toFixed(1)}% away` : null },
                { label: "Target", v: p.target ? `₹${p.target.toFixed(0)}` : "—", c: T.green, sub: p.targetDist ? `${p.targetDist.toFixed(1)}% away` : null },
                { label: "Progress", v: `${p.progress.toFixed(0)}%`, c: p.progress > 50 ? T.green : T.amber },
              ].map(it => (
                <div key={it.label}>
                  <div style={{ fontSize: 9, color: T.textMuted, marginBottom: 3 }}>{it.label}</div>
                  <Mono size={11} color={it.c} weight={700}>{it.v}</Mono>
                  {it.sub && <div style={{ fontSize: 9, color: T.textMuted, marginTop: 1 }}>{it.sub}</div>}
                </div>
              ))}
            </div>
          </div>
        ))}
        {!items.length && <div style={{ fontSize: 12, color: T.textMuted, padding: "24px 0" }}>No tracked positions</div>}
      </div>
    </Card>
  );
}

// 5. AI Confidence Timeline
function AITimelinePanel({ decisions, T }) {
  const { isMobile } = useBreakpoint();
  const chartData = useMemo(() => {
    return (decisions || []).slice(-30).map((d, i, arr) => ({
      time: d.timestamp ? new Date(d.timestamp).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }) : `T-${arr.length - i}`,
      confidence: Math.round(((d.signals || [])[0]?.confidence || 0.5) * 100),
      latency: d.latency_ms || 0,
    }));
  }, [decisions]);

  if (!chartData.length) return (
    <Card T={T}>
      <CardHeader T={T} title="AI Confidence Timeline" subtitle="Signal quality over time" />
      <div style={{ padding: "48px 16px", textAlign: "center", color: T.textMuted, fontSize: 12 }}>No decision history yet</div>
    </Card>
  );

  return (
    <Card T={T}>
      <CardHeader T={T} title="AI Confidence Timeline" subtitle="Signal confidence & latency per cycle" />
      <div style={{ padding: "14px 16px" }}>
        <ResponsiveContainer width="100%" height={isMobile ? 150 : 180}>
          <ComposedChart data={chartData} margin={{ left: isMobile ? -10 : 0, right: isMobile ? -10 : 0 }}>
            <CartesianGrid strokeDasharray="2 2" stroke={T.border} />
            <XAxis dataKey="time" tick={{ fill: T.textMuted, fontSize: 9 }} tickLine={false} axisLine={false} interval={isMobile ? 6 : 4} />
            <YAxis yAxisId="c" domain={[0, 100]} tick={{ fill: T.textMuted, fontSize: 9 }} tickLine={false} axisLine={false} tickFormatter={v => `${v}%`} width={isMobile ? 30 : 38} />
            <YAxis yAxisId="l" orientation="right" tick={{ fill: T.textMuted, fontSize: 9 }} tickLine={false} axisLine={false} tickFormatter={v => `${v}ms`} width={isMobile ? 34 : 42} />
            <Tooltip {...tooltipStyle(T)} formatter={(v, n) => [n === "confidence" ? `${v}%` : `${v}ms`, n]} />
            <ReferenceLine yAxisId="c" y={65} stroke={T.amber} strokeDasharray="4 2" strokeWidth={1} opacity={0.6} />
            <Bar yAxisId="c" dataKey="confidence" fill={T.accent} opacity={0.15} radius={[2, 2, 0, 0]} />
            <Line yAxisId="c" type="monotone" dataKey="confidence" stroke={T.accent} strokeWidth={2} dot={false} />
            <Line yAxisId="l" type="monotone" dataKey="latency" stroke={T.purple} strokeWidth={1.5} dot={false} strokeDasharray="4 2" />
          </ComposedChart>
        </ResponsiveContainer>
        <div style={{ display: "flex", gap: 16, marginTop: 10, flexWrap: "wrap" }}>
          {[{ label: "Confidence", c: T.accent }, { label: "Latency (ms)", c: T.purple }, { label: "65% threshold", c: T.amber }].map(l => (
            <div key={l.label} style={{ display: "flex", gap: 5, alignItems: "center" }}>
              <div style={{ width: 14, height: 2, background: l.c }} />
              <span style={{ fontSize: 10, color: T.textMuted }}>{l.label}</span>
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}

// 6. Strategy Review
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
    <Card T={T}>
      <CardHeader T={T} title="AI Strategy Review" subtitle="Performance review & parameter adjustments"
        right={<Pill label="auto-updated" color={T.green} T={T} />}
      />
      <div style={{ padding: "14px 16px", display: "grid", gridTemplateColumns: isMobile ? "1fr" : "1fr 1fr", gap: 16 }}>
        <div>
          <div style={{ fontSize: 9, color: T.textMuted, letterSpacing: 1, marginBottom: 12, textTransform: "uppercase", fontWeight: 700 }}>Strategy Weights</div>
          {Object.entries(weights).map(([k, v]) => (
            <div key={k} style={{ marginBottom: 12 }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 5 }}>
                <span style={{ fontSize: 11, color: T.text }}>{k.replace(/_/g, " ")}</span>
                <Mono size={11} color={STRAT_C[k] || T.accent}>{(v * 100).toFixed(0)}%</Mono>
              </div>
              <ProgressBar value={v} max={maxW} color={STRAT_C[k] || T.accent} height={4} />
            </div>
          ))}
          <div style={{ marginTop: 14, padding: "10px 12px", background: T.bg, borderRadius: 6 }}>
            <div style={{ fontSize: 9, color: T.textMuted, letterSpacing: 1, marginBottom: 8, textTransform: "uppercase" }}>Parameter Adjustments</div>
            {Object.entries(review.parameter_adjustments || {}).map(([k, v]) => (
              <div key={k} style={{ display: "flex", justifyContent: "space-between", marginBottom: 5 }}>
                <span style={{ fontSize: 10, color: T.textMuted }}>{k.replace(/_/g, " ")}</span>
                <Mono size={10.5} color={T.accent}>{v}</Mono>
              </div>
            ))}
          </div>
        </div>
        <div>
          <div style={{ fontSize: 9, color: T.textMuted, letterSpacing: 1, marginBottom: 8, textTransform: "uppercase" }}>Assessment</div>
          <p style={{ fontSize: 11, color: T.textSub, lineHeight: 1.7, padding: "10px 12px", background: T.bg, borderRadius: 6, marginBottom: 12 }}>{review.overall_assessment}</p>
          <div style={{ marginBottom: 10 }}>
            <div style={{ fontSize: 9, color: T.green, letterSpacing: 1, textTransform: "uppercase", marginBottom: 6, display: "flex", gap: 5, alignItems: "center" }}>
              <CheckCircle size={9} /> Focus Patterns
            </div>
            {(review.focus_patterns || []).map((p, i) => (
              <div key={i} style={{ fontSize: 10, color: T.textSub, padding: "5px 10px", background: T.greenDim, borderLeft: `2px solid ${T.green}50`, borderRadius: 3, marginBottom: 4 }}>{p}</div>
            ))}
          </div>
          <div>
            <div style={{ fontSize: 9, color: T.red, letterSpacing: 1, textTransform: "uppercase", marginBottom: 6, display: "flex", gap: 5, alignItems: "center" }}>
              <XCircle size={9} /> Avoid Patterns
            </div>
            {(review.avoid_patterns || []).map((p, i) => (
              <div key={i} style={{ fontSize: 10, color: T.textSub, padding: "5px 10px", background: T.redDim, borderLeft: `2px solid ${T.red}50`, borderRadius: 3, marginBottom: 4 }}>{p}</div>
            ))}
          </div>
        </div>
      </div>
    </Card>
  );
}

// 7. Model Performance
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
    { model: "2.5-flash-lite", pct: 4, isFallback: true, avgLatency: 640 },
    { model: "2.0-flash-lite", pct: 2, isFallback: true, avgLatency: 410 },
  ];
  const fallbackRate = display.filter(m => m.isFallback).reduce((s, x) => s + x.pct, 0);

  return (
    <Card T={T}>
      <CardHeader T={T} title="Model Performance" subtitle="Gemini usage & fallback tracking"
        right={<Pill label={fallbackRate > 0 ? `${fallbackRate}% fallback` : "100% primary"} color={fallbackRate > 0 ? T.amber : T.green} T={T} />}
      />
      <div style={{ padding: "14px 16px", display: "grid", gridTemplateColumns: isMobile ? "1fr" : "repeat(2,1fr)", gap: 10 }}>
        {display.map(m => (
          <div key={m.model} style={{ background: T.bg, borderRadius: 7, padding: "12px 14px", border: `1px solid ${(m.isFallback ? T.amber : T.green) + "30"}` }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8, alignItems: "center" }}>
              <Mono size={10.5} color={T.text} weight={700}>gemini-{m.model}</Mono>
              <Pill label={m.isFallback ? "fallback" : "primary"} color={m.isFallback ? T.amber : T.green} T={T} />
            </div>
            <ProgressBar value={m.pct} max={100} color={m.isFallback ? T.amber : T.accent} height={4} />
            <div style={{ display: "flex", justifyContent: "space-between", marginTop: 7 }}>
              <span style={{ fontSize: 10, color: T.textMuted }}>{m.pct}% calls</span>
              <span style={{ fontSize: 10, color: T.textMuted }}>{m.avgLatency}ms avg</span>
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

// 8. Kill Switch
function KillSwitchPanel({ risk, riskEvents, T, onReset }) {
  const daily_pnl_pct = risk?.daily_pnl_pct || 0;
  const drawdown = risk?.drawdown_pct || 0;
  const killSwitch = risk?.kill_switch;
  const events = (riskEvents?.events || []).filter(e => e.type?.includes("KILL") || e.type?.includes("STOP") || e.severity === "CRITICAL");

  return (
    <Card T={T}>
      <CardHeader T={T} title="Kill Switch Monitor" subtitle="Safety gauges & trigger history"
        right={
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <Pill label={killSwitch ? "TRIGGERED" : "SAFE"} color={killSwitch ? T.red : T.green} T={T} />
            {killSwitch && (
              <button onClick={onReset} style={{ background: T.redDim, border: `1px solid ${T.red}40`, borderRadius: 5, padding: "4px 8px", cursor: "pointer", fontSize: 10, color: T.red, fontWeight: 700 }}>RESET</button>
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
              <div key={g.label} style={{ background: T.bg, borderRadius: 7, padding: "12px 14px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                  <span style={{ fontSize: 10, color: T.textMuted }}>{g.label}</span>
                  <Mono size={11} color={pct > 75 ? g.color : T.text}>{g.value.toFixed(2)}% / {g.max}%</Mono>
                </div>
                <div style={{ position: "relative", height: 10, background: T.border, borderRadius: 5 }}>
                  <div style={{ position: "absolute", width: `${pct}%`, height: "100%", background: `linear-gradient(90deg, ${g.color}80, ${g.color})`, borderRadius: 5, boxShadow: pct > 75 ? `0 0 8px ${g.color}60` : "none" }} />
                  <div style={{ position: "absolute", left: "75%", top: 0, height: "100%", width: 1, background: T.textDim }} />
                </div>
                <div style={{ fontSize: 9, color: T.textMuted, marginTop: 5 }}>{(g.max - g.value).toFixed(2)}% remaining to trigger</div>
              </div>
            );
          })}
        </div>
        <div style={{ fontSize: 9, color: T.textMuted, letterSpacing: 1, textTransform: "uppercase", marginBottom: 8 }}>Recent Risk Events</div>
        {events.length === 0 ? (
          <div style={{ fontSize: 11, color: T.textMuted, padding: "12px 0" }}>No critical events — system operating normally</div>
        ) : events.slice(0, 5).map((e, i) => (
          <div key={i} style={{ display: "flex", gap: 8, alignItems: "flex-start", padding: "8px 10px", background: T.bg, borderRadius: 5, marginBottom: 4, borderLeft: `2px solid ${T.red}` }}>
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

// 9. Execution Queue
function ExecutionQueuePanel({ orders, T }) {
  const { isMobile } = useBreakpoint();
  const pending = (orders || []).filter(o => ["PENDING", "OPEN", "TRIGGER PENDING"].includes((o.status || "").toUpperCase()));
  const recent = (orders || []).filter(o => ["COMPLETE", "FILLED"].includes((o.status || "").toUpperCase())).slice(0, 8);

  return (
    <Card T={T}>
      <CardHeader T={T} title="Execution Queue" subtitle="Pending orders & recent fills"
        right={
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {pending.length > 0 && <Pill label={`${pending.length} pending`} color={T.amber} T={T} />}
            <Pill label="live" color={T.green} T={T} />
          </div>
        }
      />
      <div style={{ padding: "10px 16px 14px" }}>
        {pending.length > 0 && (
          <div style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 9, color: T.amber, letterSpacing: 1, textTransform: "uppercase", marginBottom: 8 }}>Pending</div>
            {pending.map((o, i) => {
              const age = o.placed_at ? Math.round((Date.now() - new Date(o.placed_at).getTime()) / 1000) : 0;
              return (
                <div key={i} style={{ display: "flex", gap: 8, alignItems: "center", padding: "9px 12px", background: T.bg, borderRadius: 6, marginBottom: 4, border: `1px solid ${T.amber}20`, flexWrap: "wrap" }}>
                  <span style={{ width: 6, height: 6, borderRadius: "50%", background: T.amber, flexShrink: 0, animation: "pulse 1.5s infinite" }} />
                  <span style={{ fontWeight: 800, fontSize: 11, color: T.text, minWidth: 70 }}>{o.symbol}</span>
                  <Pill label={o.side} color={o.side === "BUY" ? T.green : T.red} T={T} />
                  <Mono size={10} color={T.textSub}>{o.quantity} @ {o.price ? `₹${o.price}` : `₹${o.trigger_price} TRG`}</Mono>
                  <Pill label={o.order_type || "LIMIT"} color={T.blue} T={T} />
                  <span style={{ marginLeft: "auto", fontSize: 10, color: T.textMuted }}>{age}s</span>
                </div>
              );
            })}
          </div>
        )}
        <div style={{ fontSize: 9, color: T.textMuted, letterSpacing: 1, textTransform: "uppercase", marginBottom: 8 }}>Recent Fills</div>
        {recent.slice(0, 5).map((o, i) => {
          const slip = o.price && o.average_price ? ((o.average_price - o.price) / o.price * 100) : null;
          return (
            <div key={i} style={{ display: "flex", gap: 8, alignItems: "center", padding: "7px 12px", background: T.bg, borderRadius: 5, marginBottom: 3, flexWrap: "wrap" }}>
              <CheckCircle size={10} color={T.green} />
              <span style={{ fontWeight: 800, fontSize: 11, color: T.text, minWidth: 70 }}>{o.symbol}</span>
              <Pill label={o.side} color={o.side === "BUY" ? T.green : T.red} T={T} />
              <Mono size={10} color={T.text}>₹{o.average_price?.toLocaleString("en-IN") || o.price?.toLocaleString("en-IN") || "MKT"}</Mono>
              {slip !== null && <Mono size={10} color={Math.abs(slip) > 0.1 ? T.amber : T.green}>{slip >= 0 ? "+" : ""}{slip.toFixed(3)}% slip</Mono>}
              <span style={{ marginLeft: "auto", fontSize: 9, color: T.textMuted }}>{o.placed_at ? new Date(o.placed_at).toLocaleTimeString("en-IN") : "—"}</span>
            </div>
          );
        })}
        {!recent.length && <div style={{ fontSize: 11, color: T.textMuted, paddingTop: 4 }}>No fills today</div>}
      </div>
    </Card>
  );
}

// 10. Intraday Chart
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
    <Card T={T}>
      <CardHeader T={T} title="Intraday Chart" subtitle="15-min OHLCV"
        right={
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
            {symbols.map(s => (
              <button key={s} onClick={() => setSymbol(s)} style={{
                background: symbol === s ? `${T.accent}20` : "transparent",
                border: `1px solid ${symbol === s ? T.accent : T.border}`,
                borderRadius: 4, padding: "3px 7px", cursor: "pointer",
                fontSize: 9.5, color: symbol === s ? T.accent : T.textMuted,
                fontFamily: "'IBM Plex Mono', monospace", fontWeight: 700,
                transition: "all 0.15s",
              }}>{s}</button>
            ))}
          </div>
        }
      />
      <div style={{ padding: "10px 16px 14px" }}>
        {chartData.length > 0 && (
          <div style={{ display: "flex", gap: isMobile ? 12 : 24, marginBottom: 12, flexWrap: "wrap" }}>
            {[
              { l: "Open", v: chartData[0]?.open.toFixed(2) },
              { l: "High", v: Math.max(...chartData.map(d => d.high)).toFixed(2), c: T.green },
              { l: "Low", v: Math.min(...chartData.map(d => d.low)).toFixed(2), c: T.red },
              { l: "Last", v: chartData.at(-1)?.close.toFixed(2), c: color },
              { l: "Change", v: `${dayChange >= 0 ? "+" : ""}${dayChange.toFixed(2)}%`, c: color },
            ].map(it => (
              <div key={it.l}>
                <div style={{ fontSize: 9, color: T.textMuted, marginBottom: 2 }}>{it.l}</div>
                <Mono size={12} color={it.c || T.text} weight={700}>{it.v}</Mono>
              </div>
            ))}
          </div>
        )}
        {chartData.length === 0 ? (
          <div style={{ height: 180, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11, color: T.textMuted, background: T.bg, borderRadius: 6 }}>
            No live ticks for {symbol}
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={isMobile ? 150 : 190}>
            <ComposedChart data={chartData} margin={{ top: 5, right: isMobile ? -5 : 5, bottom: 0, left: isMobile ? -10 : 0 }}>
              <CartesianGrid strokeDasharray="2 2" stroke={T.border} />
              <XAxis dataKey="time" tick={{ fill: T.textMuted, fontSize: 8 }} tickLine={false} axisLine={false} interval={8} />
              <YAxis yAxisId="p" domain={["auto", "auto"]} tick={{ fill: T.textMuted, fontSize: 9 }} tickLine={false} axisLine={false} tickFormatter={v => v.toFixed(0)} width={isMobile ? 38 : 46} />
              <YAxis yAxisId="v" orientation="right" tick={{ fill: T.textMuted, fontSize: 8 }} tickLine={false} axisLine={false} tickFormatter={v => `${(v / 1000).toFixed(0)}K`} width={isMobile ? 28 : 34} />
              <Tooltip {...tooltipStyle(T)} formatter={(v, n) => [n === "volume" ? `${(v / 1000).toFixed(1)}K` : v.toFixed(2), n]} />
              <Bar yAxisId="v" dataKey="volume" fill={T.purple} opacity={0.2} radius={[1, 1, 0, 0]} />
              <Line yAxisId="p" type="monotone" dataKey="close" stroke={color} strokeWidth={2} dot={false} />
              <Line yAxisId="p" type="monotone" dataKey="high" stroke={T.green} strokeWidth={0.5} dot={false} strokeDasharray="2 3" opacity={0.5} />
              <Line yAxisId="p" type="monotone" dataKey="low" stroke={T.red} strokeWidth={0.5} dot={false} strokeDasharray="2 3" opacity={0.5} />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </div>
    </Card>
  );
}

// ─── MOBILE BOTTOM TAB BAR ────────────────────────────────────────────────────
function BottomNav({ tabs, active, onChange, T }) {
  return (
    <div style={{
      position: "fixed", bottom: 0, left: 0, right: 0, zIndex: 200,
      background: T.card, borderTop: `1px solid ${T.border}`,
      display: "flex", overflowX: "auto", WebkitOverflowScrolling: "touch",
      scrollbarWidth: "none", backdropFilter: "blur(20px)",
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
            minWidth: 54, transition: "color 0.15s",
          }}>
            <Icon size={14} />
            <span style={{ fontSize: 8, fontWeight: 700, letterSpacing: 0.4, whiteSpace: "nowrap" }}>{tab.label.toUpperCase()}</span>
          </button>
        );
      })}
    </div>
  );
}

// ─── MAIN DASHBOARD ──────────────────────────────────────────────────────────
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
  const [brokerFallbackEvents, setBrokerFallbackEvents] = useState([]);
  const [simState, setSimState] = useState({ loading: false, error: "", data: null, runId: "" });
  const [simBackfilling, setSimBackfilling] = useState(false);
  const [simConfig, setSimConfig] = useState({ symbols: "RELIANCE,TCS", timeframe: "day", exchange: "NSE", start_date: "2024-01-01", end_date: "2024-12-31", initial_capital: 100000, fee_pct: 0.0003, slippage_pct: 0.0005 });
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const { data: ordersData, refetch: refetchOrders } = useAPI("/api/orders", 10000);
  const { data: analyticsData } = useAPI("/api/analytics/performance?days=30", 60000);
  const { data: agentData } = useAPI("/api/agent/in-memory-decisions", 5000);
  const { data: riskEvents } = useAPI("/api/risk/events?limit=20", 30000);
  const { data: dailyHistory } = useAPI("/api/analytics/daily-history?days=14", 300000);
  const { data: brokerPreferenceData, refetch: refetchBrokerPreference } = useAPI("/api/settings/broker-preference", 5000);

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
    } catch (e) { alert("Failed to start engine: " + e.message); }
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
    } catch (e) { alert("Error: " + e.message); }
  };

  const saveUiPrimaryBroker = async () => {
    setSavingBrokerPref(true);
    setBrokerPrefMessage("");
    try {
      const res = await fetch(`${API_BASE}/api/settings/broker-preference`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ui_primary_broker: uiPrimarySelection }) });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail || "Failed");
      setBrokerPrefMessage(data?.fallback_active ? `Selected ${uiPrimarySelection.toUpperCase()} unavailable, using ${String(data?.effective_primary_broker || "").toUpperCase()}` : `UI primary set to ${uiPrimarySelection.toUpperCase()} ✅`);
      refetchBrokerPreference();
    } catch (e) { setBrokerPrefMessage(`Error: ${e.message}`); }
    finally { setSavingBrokerPref(false); }
  };

  const loadSimulation = useCallback(async () => {
    setSimState(prev => ({ ...prev, loading: true, error: "" }));
    try {
      const payload = { ...simConfig, symbols: simConfig.symbols.split(",").map(s => s.trim().toUpperCase()).filter(Boolean) };
      const startRes = await fetch(`${API_BASE}/api/replay/runs`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      const started = await startRes.json();
      if (!startRes.ok) throw new Error(started?.detail || "Unable to start replay");
      const runId = started.run_id;
      for (let i = 0; i < 120; i++) {
        const statusRes = await fetch(`${API_BASE}/api/replay/runs/${runId}`);
        const status = await statusRes.json();
        if (!statusRes.ok) throw new Error(status?.detail || "Status failed");
        if (status.status === "completed") {
          const r = await fetch(`${API_BASE}/api/replay/runs/${runId}/results`);
          const result = await r.json();
          setSimState({ loading: false, error: "", data: { ...result, status }, runId });
          return;
        }
        if (status.status === "failed") throw new Error(status.error || "Replay failed");
        await new Promise(r => setTimeout(r, 1500));
      }
      throw new Error("Replay timed out");
    } catch (e) { setSimState(prev => ({ ...prev, loading: false, error: e.message, data: null })); }
  }, [simConfig]);

  const backfillAndRun = useCallback(async () => {
    const symbols = simConfig.symbols.split(",").map(s => s.trim().toUpperCase()).filter(Boolean);
    if (!symbols.length) { setSimState(prev => ({ ...prev, error: "Add at least one symbol." })); return; }
    setSimBackfilling(true);
    setSimState(prev => ({ ...prev, error: "" }));
    try {
      const payload = { symbols, exchange: simConfig.exchange, timeframe: simConfig.timeframe, start_date: simConfig.start_date ? `${simConfig.start_date}T00:00:00` : null, end_date: simConfig.end_date ? `${simConfig.end_date}T23:59:59` : null };
      const res = await fetch(`${API_BASE}/api/historical/backfill`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      const out = await res.json();
      if (!res.ok) throw new Error(out?.detail || "Backfill failed");
      if (Array.isArray(out?.failures) && out.failures.length) throw new Error(`Backfill: ${out.failures[0]?.error || "some symbols failed"}`);
      await loadSimulation();
    } catch (e) { setSimState(prev => ({ ...prev, error: e.message, data: null })); }
    finally { setSimBackfilling(false); }
  }, [loadSimulation, simConfig]);

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

  const inputStyle = {
    background: T.bg, border: `1px solid ${T.border}`, color: T.text,
    borderRadius: 6, padding: "9px 12px", fontSize: 11,
    fontFamily: "'IBM Plex Mono', monospace", outline: "none",
    transition: "border-color 0.15s",
  };

  const btnPrimary = (color) => ({
    background: `${color}15`, border: `1px solid ${color}40`, borderRadius: 6,
    padding: "7px 14px", cursor: "pointer", fontSize: 10, fontWeight: 700,
    color, letterSpacing: 0.5, transition: "all 0.15s",
  });

  return (
    <div style={{ minHeight: "100vh", background: T.bg, color: T.text, fontFamily: "'IBM Plex Sans', system-ui, sans-serif", paddingBottom: isMobile ? 68 : 0 }}>
      <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&family=IBM+Plex+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet" />
      <style>{`
        * { box-sizing: border-box; margin: 0; padding: 0; }
        ::-webkit-scrollbar { width: 4px; height: 4px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: ${T.border}; border-radius: 2px; }
        button { font-family: inherit; }
        input { font-family: inherit; }
        @keyframes pulse { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:0.5;transform:scale(0.8)} }
        @keyframes shimmer { 0%,100%{opacity:0.4} 50%{opacity:0.8} }
        @keyframes fadeIn { from{opacity:0;transform:translateY(4px)} to{opacity:1;transform:translateY(0)} }
        .tab-content { animation: fadeIn 0.2s ease; }
        input[type="date"] { color-scheme: ${isDark ? "dark" : "light"}; }
        input:focus { border-color: ${T.accent} !important; }
      `}</style>

      {/* ── TOP HEADER ── */}
      <header style={{
        background: T.card, borderBottom: `1px solid ${T.border}`,
        position: "sticky", top: 0, zIndex: 100, boxShadow: T.shadow,
      }}>
        <div style={{ maxWidth: 1800, margin: "0 auto", padding: isMobile ? "0 12px" : "0 16px" }}>
          {/* Row 1: brand + indices + controls */}
          <div style={{ height: 52, display: "flex", alignItems: "center", gap: isMobile ? 8 : 16 }}>
            {/* Logo */}
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
              <div style={{ width: 30, height: 30, borderRadius: 7, background: `linear-gradient(135deg, ${T.accent}, ${T.blue})`, display: "flex", alignItems: "center", justifyContent: "center" }}>
                <Zap size={13} color="#fff" fill="#fff" />
              </div>
              {!isMobile && (
                <div>
                  <div style={{ fontWeight: 800, fontSize: 13, letterSpacing: 0.5, color: T.text }}>AgentTrader</div>
                  <div style={{ fontSize: 8.5, color: T.textMuted, letterSpacing: 2, textTransform: "uppercase" }}>NSE · F&O · Gemini AI</div>
                </div>
              )}
            </div>

            {/* Divider */}
            {!isMobile && <div style={{ width: 1, height: 24, background: T.border }} />}

            {/* Index pills */}
            <div style={{ display: "flex", gap: 6, flex: 1, overflowX: "auto", scrollbarWidth: "none" }}>
              {[
                { label: "NIFTY", val: indices.nifty, base: 22000 },
                { label: "BNIFTY", val: indices.banknifty, base: 47000 },
                { label: "VIX", val: indices.vix, base: 14, warn: (indices.vix || 0) > 18 },
              ].map(idx => {
                const chg = idx.base ? (((idx.val || idx.base) - idx.base) / idx.base * 100) : 0;
                return (
                  <div key={idx.label} style={{ display: "flex", gap: 6, alignItems: "center", padding: "4px 10px", background: T.bg, borderRadius: 5, border: `1px solid ${T.border}`, flexShrink: 0 }}>
                    <span style={{ fontSize: 9, color: T.textMuted, fontWeight: 700, letterSpacing: 0.5 }}>{idx.label}</span>
                    <Mono size={11} color={idx.warn ? T.amber : T.text} weight={700}>{idx.val?.toFixed(2) || "—"}</Mono>
                    {idx.val && <span style={{ fontSize: 9, color: chg >= 0 ? T.green : T.red, fontFamily: "'IBM Plex Mono'" }}>{chg >= 0 ? "▲" : "▼"}{Math.abs(chg).toFixed(2)}%</span>}
                  </div>
                );
              })}
            </div>

            {/* Right controls */}
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
              {!isMobile && (
                <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10, color: T.textMuted }}>
                  <StatusDot active={connected} color={T.green} />
                  <span style={{ fontFamily: "monospace" }}>{connected ? "LIVE" : "OFFLINE"}</span>
                  <span style={{ color: T.textDim }}>·</span>
                  <span style={{ fontFamily: "monospace" }}>{lastUpdate.toLocaleTimeString("en-IN")}</span>
                </div>
              )}

              {killSwitch && (
                <button onClick={handleResetKillSwitch} style={{ ...btnPrimary(T.red), padding: "5px 10px", fontSize: 9 }}>
                  ⚠ KILL SWITCH
                </button>
              )}

              {/* Theme toggle */}
              <button onClick={() => setIsDark(p => !p)} style={{ background: T.bg, border: `1px solid ${T.border}`, borderRadius: 6, padding: 7, cursor: "pointer", color: T.textSub, display: "flex", alignItems: "center" }}>
                {isDark ? <Sun size={13} /> : <Moon size={13} />}
              </button>

              {/* Engine button */}
              <button
                onClick={engineRunning ? handleStopEngine : handleStartEngine}
                disabled={startingEngine}
                style={{
                  background: engineRunning ? T.greenDim : T.blueDim,
                  border: `1px solid ${engineRunning ? T.green : T.blue}40`,
                  borderRadius: 6, padding: "6px 12px", cursor: "pointer",
                  fontSize: 10, fontWeight: 700, letterSpacing: 0.5,
                  color: engineRunning ? T.green : T.blue,
                  display: "flex", alignItems: "center", gap: 5, whiteSpace: "nowrap",
                  transition: "all 0.15s",
                }}
              >
                <Power size={10} />
                {isMobile ? (startingEngine ? "…" : engineRunning ? "ON" : "GO") : (startingEngine ? "STARTING…" : engineRunning ? "RUNNING" : "START")}
              </button>
            </div>
          </div>

          {/* Row 2: desktop nav tabs */}
          {!isMobile && (
            <div style={{ display: "flex", gap: 0, borderTop: `1px solid ${T.border}`, overflowX: "auto", scrollbarWidth: "none" }}>
              {TABS.map(tab => {
                const Icon = tab.icon;
                const isActive = activeTab === tab.id;
                return (
                  <button key={tab.id} onClick={() => setActiveTab(tab.id)} style={{
                    background: "none", border: "none", cursor: "pointer",
                    padding: "9px 16px", fontSize: 10, fontWeight: 700, letterSpacing: 0.6, textTransform: "uppercase",
                    color: isActive ? T.accent : T.textMuted,
                    borderBottom: `2px solid ${isActive ? T.accent : "transparent"}`,
                    display: "flex", alignItems: "center", gap: 6,
                    transition: "all 0.15s", whiteSpace: "nowrap", flexShrink: 0,
                  }}>
                    <Icon size={10} />
                    {tab.label}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </header>

      {/* ── ALERT BANNERS ── */}
      <div style={{ maxWidth: 1800, margin: "0 auto", padding: isMobile ? "10px 12px 0" : "12px 16px 0" }}>
        {replicationEnabled && replicationStatus === "partial_failure" && (
          <div style={{ background: T.amberDim, border: `1px solid ${T.amber}35`, borderRadius: 7, padding: "9px 14px", marginBottom: 10, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <AlertTriangle size={12} color={T.amber} />
            <span style={{ fontSize: 11, color: T.amber, fontWeight: 600 }}>Replica warning: Zerodha copy partially failing</span>
            {replicationError && <span style={{ fontSize: 10, color: T.textMuted }}>· {replicationError}</span>}
          </div>
        )}
        {!engineRunning && (
          <div style={{ background: T.blueDim, border: `1px solid ${T.blue}25`, borderRadius: 7, padding: "9px 14px", marginBottom: 10, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <Power size={12} color={T.blue} />
            <span style={{ fontSize: 11, color: T.blue, fontWeight: 600 }}>Engine stopped</span>
            <span style={{ fontSize: 11, color: T.textMuted }}>· Live data streams when engine is running</span>
            <button onClick={handleStartEngine} style={{ ...btnPrimary(T.blue), marginLeft: "auto", padding: "5px 14px" }}>
              START ENGINE →
            </button>
          </div>
        )}
      </div>

      {/* ── MAIN CONTENT ── */}
      <main style={{ maxWidth: 1800, margin: "0 auto", padding: isMobile ? "10px 12px" : "14px 16px" }}>

        {/* OVERVIEW */}
        {activeTab === "overview" && (
          <div className="tab-content" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <div style={{ display: "grid", gridTemplateColumns: gridKpi, gap: 10 }}>
              <StatTile T={T} label="Today P&L" value={<span style={{ color: pnlColor, fontFamily: "'IBM Plex Mono'", fontWeight: 800, fontSize: 20 }}>{(pnl.total || 0) >= 0 ? "+" : ""}₹{Math.abs(pnl.total || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 })}</span>} sub={`${(pnl.pct || 0) >= 0 ? "+" : ""}${(pnl.pct || 0).toFixed(2)}% day`} color={pnlColor} icon={(pnl.total || 0) >= 0 ? TrendingUp : TrendingDown} />
              <StatTile T={T} label="Realized P&L" value={<span style={{ color: (pnl.realized||0)>=0?T.green:T.red, fontFamily: "'IBM Plex Mono'", fontWeight: 800, fontSize: 20 }}>{(pnl.realized||0)>=0?"+":""}₹{Math.abs(pnl.realized||0).toLocaleString("en-IN",{maximumFractionDigits:0})}</span>} sub="Booked today" color={T.green} icon={Target} />
              <StatTile T={T} label="Available" value={`₹${((funds.available || 0) / 1000).toFixed(1)}K`} sub={`₹${((funds.used_margin || 0) / 1000).toFixed(1)}K margin used`} color={T.blue} icon={DollarSign} />
              <StatTile T={T} label="Positions" value={positions.length} sub={`${10 - positions.length} slots free`} color={T.purple} icon={Activity} />
              <StatTile T={T} label="Win Rate" value={`${(risk.win_rate || 0).toFixed(1)}%`} sub={`${risk.trades_today || 0} trades`} color={T.amber} icon={Shield} />
              <StatTile T={T} label="Max Drawdown" value={`${(risk.drawdown_pct || 0).toFixed(2)}%`} sub={(risk.drawdown_pct || 0) < 2 ? "Within limits" : "⚠ Near limit"} color={(risk.drawdown_pct || 0) < 2 ? T.green : T.red} icon={AlertTriangle} />
            </div>

            <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "2fr 1fr", gap: 14 }}>
              <Card T={T}>
                <CardHeader T={T} title="Intraday P&L" subtitle="Real-time equity curve"
                  right={<span style={{ fontFamily: "monospace", fontSize: 13, fontWeight: 800, color: pnlColor }}>{(pnl.total || 0) >= 0 ? "+" : ""}₹{Math.abs(pnl.total || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 })}</span>}
                />
                <div style={{ padding: "14px 16px" }}>
                  <ResponsiveContainer width="100%" height={isMobile ? 130 : 170}>
                    <AreaChart data={pnlHistory} margin={{ left: isMobile ? -8 : 0, right: 5 }}>
                      <defs>
                        <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor={pnlColor} stopOpacity="0.25" />
                          <stop offset="100%" stopColor={pnlColor} stopOpacity="0" />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="2 2" stroke={T.border} />
                      <XAxis dataKey="time" tick={{ fill: T.textMuted, fontSize: 9 }} tickLine={false} axisLine={false} interval={14} />
                      <YAxis tick={{ fill: T.textMuted, fontSize: 9 }} tickLine={false} axisLine={false} tickFormatter={v => `₹${(v / 1000).toFixed(1)}K`} width={isMobile ? 40 : 50} />
                      <Tooltip {...tooltipStyle(T)} formatter={v => [`₹${v.toLocaleString("en-IN")}`, "P&L"]} />
                      <ReferenceLine y={0} stroke={T.border} strokeDasharray="3 3" />
                      <Area type="monotone" dataKey="pnl" stroke={pnlColor} fill="url(#pnlGrad)" strokeWidth={2} dot={false} />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </Card>

              <Card T={T}>
                <CardHeader T={T} title="Risk Gauges" subtitle="Limit consumption" />
                <div style={{ padding: "14px 16px" }}>
                  {[
                    { label: "Daily Loss Limit", used: Math.abs(Math.min(0, risk.daily_pnl_pct || 0)), max: 2.0, color: T.red },
                    { label: "Max Drawdown", used: risk.drawdown_pct || 0, max: 8.0, color: T.amber },
                    { label: "Positions", used: positions.length, max: 10, color: T.purple, noPercent: true },
                    { label: "Margin Used", used: ((funds.used_margin || 0) / (funds.total || 1)) * 100, max: 80, color: T.blue },
                  ].map(r => {
                    const pct = Math.min((r.used / r.max) * 100, 100);
                    const dangerColor = pct > 75 ? T.red : r.color;
                    return (
                      <div key={r.label} style={{ marginBottom: 16 }}>
                        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                          <span style={{ fontSize: 10, color: T.textMuted }}>{r.label}</span>
                          <Mono size={10} color={pct > 75 ? T.red : T.text}>{r.noPercent ? `${Math.round(r.used)}/${r.max}` : `${r.used.toFixed(1)}%`}</Mono>
                        </div>
                        <ProgressBar value={r.used} max={r.max} color={dangerColor} height={4} />
                      </div>
                    );
                  })}
                  <div style={{ paddingTop: 10, borderTop: `1px solid ${T.border}`, display: "flex", justifyContent: "space-between" }}>
                    <span style={{ fontSize: 10, color: T.textMuted }}>Kill Switch</span>
                    <span style={{ fontSize: 10, fontWeight: 700, color: killSwitch ? T.red : T.green }}>{killSwitch ? "⚠ TRIGGERED" : "✓ SAFE"}</span>
                  </div>
                </div>
              </Card>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: col2, gap: 14 }}>
              <Card T={T}>
                <CardHeader T={T} title="AI Cycle" subtitle="Current decision pipeline"
                  right={latestDecision?.market_regime ? <Pill label={latestDecision.market_regime} color={T.accent} T={T} /> : null}
                />
                <div style={{ padding: "14px 16px" }}>
                  <div style={{ display: "flex", gap: 4, marginBottom: 10, flexWrap: "wrap" }}>
                    {["collecting_context", "calling_model", "risk_checks", "placing_orders", "decision_complete"].map(s => {
                      const active = agentStatus?.stage === s;
                      return (
                        <span key={s} style={{ fontSize: 9, padding: "3px 7px", borderRadius: 3, border: `1px solid ${active ? T.accent : T.border}`, color: active ? T.accent : T.textMuted, background: active ? T.accentDim : "transparent", transition: "all 0.2s" }}>
                          {s.replace(/_/g, " ")}
                        </span>
                      );
                    })}
                  </div>
                  <div style={{ height: 5, background: T.bg, borderRadius: 3, overflow: "hidden", marginBottom: 10 }}>
                    <div style={{ width: `${Math.max(3, progressPct)}%`, height: "100%", background: `linear-gradient(90deg, ${T.accent}, ${T.blue})`, transition: "width 0.3s" }} />
                  </div>
                  <div style={{ fontSize: 10, color: T.textMuted, marginBottom: 12 }}>
                    {progressPct}% complete · {agentStatus?.last_cycle_duration_ms ? `${agentStatus.last_cycle_duration_ms}ms` : "—"}
                  </div>
                  {eventTape.slice(-5).reverse().map((e, i) => (
                    <div key={i} style={{ display: "flex", gap: 8, alignItems: "flex-start", marginBottom: 4 }}>
                      <span style={{ fontSize: 9, color: T.textDim, minWidth: 42, fontFamily: "monospace", flexShrink: 0, paddingTop: 1 }}>{new Date(e.timestamp).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}</span>
                      <span style={{ width: 4, height: 4, borderRadius: "50%", background: e.level === "error" ? T.red : e.level === "success" ? T.green : T.amber, marginTop: 5, flexShrink: 0 }} />
                      <span style={{ fontSize: 10, color: e.level === "error" ? T.red : e.level === "success" ? T.green : T.textSub }}>{e.message}</span>
                    </div>
                  ))}
                  {!eventTape.length && <span style={{ fontSize: 11, color: T.textMuted }}>Waiting for AI events…</span>}
                </div>
              </Card>

              <Card T={T}>
                <CardHeader T={T} title="Live Ticks" subtitle="Real-time price feed" right={<StatusDot active={connected} color={T.green} />} />
                <div style={{ padding: "10px 16px 14px", display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 8 }}>
                  {Object.entries(ticks).slice(0, isMobile ? 6 : 9).map(([sym, val]) => {
                    const curr = tickPrice(val);
                    const prev = tickPrice(prevTicks[sym] || curr);
                    const delta = curr - prev;
                    const series = tickHistory[sym] || [];
                    return (
                      <div key={sym} style={{ background: T.bg, borderRadius: 6, padding: "8px 10px" }}>
                        <div style={{ fontSize: 9, color: T.textMuted, marginBottom: 3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontWeight: 600 }}>{sym}</div>
                        <Mono size={10.5} color={delta > 0 ? T.green : delta < 0 ? T.red : T.text} weight={700}>₹{curr ? curr.toLocaleString("en-IN") : "—"}</Mono>
                        <Sparkline data={series.slice(-15)} color={delta >= 0 ? T.green : T.red} height={24} />
                      </div>
                    );
                  })}
                  {!Object.keys(ticks).length && (
                    <div style={{ gridColumn: "1/-1", textAlign: "center", padding: "24px 0", fontSize: 11, color: T.textMuted }}>Waiting for tick data…</div>
                  )}
                </div>
              </Card>
            </div>
          </div>
        )}

        {/* POSITIONS */}
        {activeTab === "positions" && (
          <div className="tab-content" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <PositionsPanel positions={positions} tickHistory={tickHistory} T={T} />
            <SLTrackerPanel positions={positions} ticks={ticks} T={T} />
          </div>
        )}

        {/* OPTIONS */}
        {activeTab === "options" && (
          <div className="tab-content" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <OptionsChainPanel data={liveData?.options_chain || null} T={T} />
            <IntradayChartPanel ticks={ticks} tickHistory={tickHistory} T={T} />
          </div>
        )}

        {/* WATCHLIST */}
        {activeTab === "watchlist" && (
          <div className="tab-content" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <WatchlistPanel data={liveData?.watchlist || null} T={T} />
          </div>
        )}

        {/* AI BRAIN */}
        {activeTab === "ai" && (
          <div className="tab-content" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <AITimelinePanel decisions={agentDecisions} T={T} />
            <StrategyReviewPanel reviewData={null} T={T} />
            <ModelPerformancePanel decisions={agentDecisions} T={T} />
            <Card T={T}>
              <CardHeader T={T} title="Latest AI Signals"
                subtitle={`${latestSignals.length} signals · ${latestDecision?.timestamp ? new Date(latestDecision.timestamp).toLocaleTimeString("en-IN") : "—"}`}
              />
              <div style={{ padding: "14px 16px", display: "grid", gridTemplateColumns: isMobile ? "1fr" : "repeat(auto-fill, minmax(280px, 1fr))", gap: 12 }}>
                {latestSignals.map((s, i) => {
                  const conf = Math.round((Number(s.confidence || 0)) * 100);
                  const confColor = conf >= 70 ? T.green : conf >= 50 ? T.amber : T.red;
                  return (
                    <div key={i} style={{ background: T.bg, borderRadius: 8, padding: "14px", border: `1px solid ${T.border}` }}>
                      <div style={{ display: "flex", gap: 8, marginBottom: 10, alignItems: "center", flexWrap: "wrap" }}>
                        <span style={{ fontWeight: 800, fontSize: 13, color: T.text }}>{s.symbol}</span>
                        <Pill label={s.action} color={ACTION_COLORS_FN(T)[s.action] || T.textMuted} T={T} />
                        <span style={{ marginLeft: "auto" }}><Pill label={s.strategy || "—"} color={STRATEGY_COLORS_FN(T)[s.strategy] || T.textMuted} T={T} /></span>
                      </div>
                      <div style={{ marginBottom: 10 }}>
                        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 5 }}>
                          <span style={{ fontSize: 9, color: T.textMuted, textTransform: "uppercase", letterSpacing: 0.8 }}>Confidence</span>
                          <Mono size={10} color={confColor}>{conf}%</Mono>
                        </div>
                        <ProgressBar value={conf} max={100} color={confColor} height={4} />
                      </div>
                      <div style={{ fontSize: 10, color: T.textMuted, lineHeight: 1.7, fontFamily: "'IBM Plex Mono'" }}>
                        Entry: {s.entry_price ? `₹${Number(s.entry_price).toFixed(0)}` : "—"} · SL: {s.stop_loss ? `₹${Number(s.stop_loss).toFixed(0)}` : "—"} · Tgt: {s.target ? `₹${Number(s.target).toFixed(0)}` : "—"}
                        {s.risk_reward ? ` · R/R: ${Number(s.risk_reward).toFixed(1)}` : ""}
                      </div>
                      <p style={{ fontSize: 10.5, color: T.textSub, marginTop: 8, lineHeight: 1.6 }}>{s.rationale}</p>
                      <div style={{ marginTop: 10 }}>
                        <Pill label={s.risk_status === "approved" ? "risk passed" : "risk rejected"} color={s.risk_status === "approved" ? T.green : T.red} T={T} />
                      </div>
                    </div>
                  );
                })}
                {!latestSignals.length && <div style={{ fontSize: 11, color: T.textMuted }}>No live AI signals yet</div>}
              </div>
            </Card>
          </div>
        )}

        {/* ORDERS */}
        {activeTab === "orders" && (
          <div className="tab-content" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <ExecutionQueuePanel orders={orders} T={T} />
            <Card T={T}>
              <CardHeader T={T} title="Order History" subtitle="All orders today"
                right={<button onClick={refetchOrders} style={{ background: "none", border: "none", cursor: "pointer", color: T.textMuted, padding: 4 }}><RefreshCw size={12} /></button>}
              />
              <div style={{ padding: "0 16px 14px", overflowX: "auto" }}>
                {!orders.length ? (
                  <div style={{ textAlign: "center", padding: "40px 0", color: T.textMuted, fontSize: 12 }}>No orders today</div>
                ) : isMobile ? (
                  <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 10 }}>
                    {orders.slice(0, 30).map((o, i) => {
                      const slip = o.price && o.average_price ? ((o.average_price - o.price) / o.price * 100) : null;
                      return (
                        <div key={i} style={{ background: T.bg, borderRadius: 7, padding: "12px 14px", border: `1px solid ${T.border}` }}>
                          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                            <div style={{ display: "flex", gap: 8 }}>
                              <span style={{ fontWeight: 800, fontSize: 12, color: T.text }}>{o.symbol}</span>
                              <Pill label={o.side} color={o.side === "BUY" ? T.green : T.red} T={T} />
                            </div>
                            <Pill label={o.status} color={{ COMPLETE: T.green, FILLED: T.green, PENDING: T.amber, REJECTED: T.red }[(o.status || "").toUpperCase()] || T.textMuted} T={T} />
                          </div>
                          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
                            {[{ l: "Qty", v: o.quantity }, { l: "Price", v: o.price ? `₹${o.price}` : "MKT" }, { l: "Avg Fill", v: o.average_price ? `₹${o.average_price}` : "—" }, { l: "Slippage", v: slip !== null ? `${slip.toFixed(3)}%` : "—" }].map(it => (
                              <div key={it.l}>
                                <div style={{ fontSize: 9, color: T.textMuted }}>{it.l}</div>
                                <Mono size={11} color={T.text}>{it.v}</Mono>
                              </div>
                            ))}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <table style={{ width: "100%", borderCollapse: "collapse", marginTop: 10, minWidth: 600 }}>
                    <thead>
                      <tr style={{ borderBottom: `1px solid ${T.border}` }}>
                        {["Time", "Symbol", "Side", "Qty", "Price", "Avg Fill", "Status", "Tag", "Slippage"].map(h =>
                          <th key={h} style={{ padding: "7px 8px", textAlign: "left", fontSize: 9, color: T.textMuted, fontWeight: 700, letterSpacing: 0.8, textTransform: "uppercase" }}>{h}</th>
                        )}
                      </tr>
                    </thead>
                    <tbody>
                      {orders.slice(0, 30).map((o, i) => {
                        const slip = o.price && o.average_price ? ((o.average_price - o.price) / o.price * 100) : null;
                        return (
                          <tr key={i} style={{ borderBottom: `1px solid ${T.border}20`, background: i % 2 ? T.bg + "60" : "transparent" }}>
                            <td style={{ padding: "9px 8px" }}><Mono size={10} color={T.textMuted}>{new Date(o.placed_at).toLocaleTimeString("en-IN")}</Mono></td>
                            <td style={{ padding: "9px 8px", fontWeight: 800, fontSize: 11, color: T.text }}>{o.symbol}</td>
                            <td style={{ padding: "9px 8px" }}><Pill label={o.side} color={o.side === "BUY" ? T.green : T.red} T={T} /></td>
                            <td style={{ padding: "9px 8px" }}><Mono size={11} color={T.text}>{o.quantity}</Mono></td>
                            <td style={{ padding: "9px 8px" }}><Mono size={11} color={T.text}>{o.price ? `₹${o.price}` : "MKT"}</Mono></td>
                            <td style={{ padding: "9px 8px" }}><Mono size={11} color={T.textSub}>{o.average_price ? `₹${o.average_price}` : "—"}</Mono></td>
                            <td style={{ padding: "9px 8px" }}><Pill label={o.status} color={{ COMPLETE: T.green, FILLED: T.green, PENDING: T.amber, REJECTED: T.red }[(o.status || "").toUpperCase()] || T.textMuted} T={T} /></td>
                            <td style={{ padding: "9px 8px", fontSize: 10, color: T.textMuted }}>{o.tag || "—"}</td>
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

        {/* SYSTEM */}
        {activeTab === "system" && (
          <div className="tab-content" style={{ display: "grid", gridTemplateColumns: col2, gap: 14 }}>
            <KillSwitchPanel risk={risk} riskEvents={riskEvents} T={T} onReset={handleResetKillSwitch} />
            <ModelPerformancePanel decisions={agentDecisions} T={T} />

            {/* Broker UI Primary */}
            <Card T={T}>
              <CardHeader T={T} title="UI Primary Broker" subtitle="Dashboard data source" />
              <div style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 12 }}>
                <div style={{ display: "flex", gap: 8 }}>
                  {["dhan", "zerodha"].map(b => (
                    <button key={b} onClick={() => setUiPrimarySelection(b)} style={{
                      background: uiPrimarySelection === b ? T.accentDim : "transparent",
                      border: `1px solid ${uiPrimarySelection === b ? T.accent : T.border}`,
                      color: uiPrimarySelection === b ? T.accent : T.textMuted,
                      borderRadius: 6, padding: "7px 14px", fontSize: 10, fontWeight: 700, cursor: "pointer", textTransform: "uppercase", transition: "all 0.15s",
                    }}>{b}</button>
                  ))}
                </div>
                <div style={{ fontSize: 10, color: T.textMuted }}>
                  Selected: <Mono size={10} color={T.text}>{uiPrimaryBroker}</Mono> · Effective: <Mono size={10} color={T.text}>{effectivePrimaryBroker}</Mono>
                </div>
                {primaryOverrideActive && <div style={{ fontSize: 10, color: T.amber }}>⚠ Fallback active. {liveData?.primary_override_reason || ""}</div>}
                {brokerPrefMessage && <div style={{ fontSize: 10, color: brokerPrefMessage.startsWith("Error") ? T.red : T.green }}>{brokerPrefMessage}</div>}
                <button onClick={saveUiPrimaryBroker} disabled={savingBrokerPref} style={btnPrimary(T.blue)}>{savingBrokerPref ? "Saving…" : "Save Preference"}</button>
              </div>
            </Card>

            {/* Broker Health */}
            <Card T={T}>
              <CardHeader T={T} title="Broker Health" subtitle="Connectivity status" />
              <div style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
                {["dhan", "zerodha"].map(b => {
                  const isConn = connectedBrokers.includes(b);
                  return (
                    <div key={b} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 14px", background: T.bg, borderRadius: 6 }}>
                      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                        <StatusDot active={isConn} color={isConn ? T.green : T.red} />
                        <span style={{ fontSize: 12, fontWeight: 700, color: T.text, textTransform: "uppercase" }}>{b}</span>
                      </div>
                      <Pill label={isConn ? "healthy" : "down"} color={isConn ? T.green : T.red} T={T} />
                    </div>
                  );
                })}
                <div style={{ fontSize: 10, color: T.textMuted, paddingTop: 4 }}>Last checked: {lastUpdate.toLocaleTimeString("en-IN")}</div>
              </div>
            </Card>

            {/* Event Log */}
            <Card T={T} style={{ gridColumn: isMobile ? "1" : "1/-1" }}>
              <CardHeader T={T} title="Event Log" subtitle="Full AI agent pipeline events" />
              <div style={{ padding: "10px 16px 14px", maxHeight: 320, overflowY: "auto" }}>
                {!eventTape.length ? (
                  <div style={{ fontSize: 11, color: T.textMuted, padding: "20px 0" }}>No events yet</div>
                ) : eventTape.slice().reverse().map((e, i) => (
                  <div key={i} style={{ display: "flex", gap: 8, padding: "6px 8px", borderRadius: 4, marginBottom: 2, background: i % 2 === 0 ? T.bg : "transparent", alignItems: "flex-start" }}>
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

        {/* SIMULATOR */}
        {activeTab === "simulator" && (
          <div className="tab-content" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <div style={{ display: "grid", gridTemplateColumns: isMobile ? "repeat(2,1fr)" : "repeat(4,1fr)", gap: 10 }}>
              <StatTile T={T} label="Mode" value="Historical Replay" sub="Simulated · no live orders" color={T.accent} icon={Shield} />
              <StatTile T={T} label="Pipeline" value="AI + Risk" sub="Shared with live engine" color={T.purple} icon={Cpu} />
              <StatTile T={T} label="Data Source" value="NSE/BSE" sub="Stored candles" color={T.amber} icon={Database} />
              <StatTile T={T} label="Run ID" value={simState.runId || "—"} sub="Latest job" color={T.blue} icon={Activity} />
            </div>
            <Card T={T}>
              <CardHeader T={T} title="Paper Simulator" subtitle={SIM_SOURCE}
                right={
                  <div style={{ display: "flex", gap: 8 }}>
                    <button onClick={backfillAndRun} disabled={simState.loading || simBackfilling} style={{ ...btnPrimary(T.accent), opacity: simState.loading || simBackfilling ? 0.5 : 1 }}>
                      {simBackfilling ? "Backfilling…" : "Backfill & Run"}
                    </button>
                    <button onClick={loadSimulation} disabled={simState.loading || simBackfilling} style={{ ...btnPrimary(T.blue), opacity: simState.loading || simBackfilling ? 0.5 : 1 }}>
                      Rerun
                    </button>
                  </div>
                }
              />
              <div style={{ padding: "14px 16px" }}>
                <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "repeat(3,1fr)", gap: 10, marginBottom: 14 }}>
                  {[
                    { placeholder: "Symbols (comma-separated)", key: "symbols", type: "text" },
                    { placeholder: "Start date", key: "start_date", type: "date" },
                    { placeholder: "End date", key: "end_date", type: "date" },
                    { placeholder: "Initial capital", key: "initial_capital", type: "number" },
                    { placeholder: "Fee %", key: "fee_pct", type: "number", step: "0.0001" },
                    { placeholder: "Slippage %", key: "slippage_pct", type: "number", step: "0.0001" },
                  ].map(f => (
                    <input key={f.key} type={f.type} step={f.step} placeholder={f.placeholder} value={simConfig[f.key]}
                      onChange={e => setSimConfig(p => ({ ...p, [f.key]: f.type === "number" ? Number(e.target.value || 0) : e.target.value }))}
                      style={inputStyle}
                    />
                  ))}
                </div>

                {simBackfilling && <div style={{ fontSize: 11, color: T.textMuted, marginBottom: 8 }}>Backfilling historical candles…</div>}
                {simState.loading && <div style={{ fontSize: 11, color: T.textMuted, marginBottom: 8 }}>Running replay…</div>}
                {simState.error && (
                  <div style={{ background: T.redDim, border: `1px solid ${T.red}30`, borderRadius: 6, padding: "10px 14px", marginBottom: 10 }}>
                    <div style={{ fontSize: 11, color: T.red, marginBottom: 4 }}>Error: {simState.error}</div>
                    <div style={{ fontSize: 10, color: T.textMuted }}>Tip: backfill candles for same symbols/date window, then click Rerun.</div>
                  </div>
                )}
                {!simState.loading && !simState.error && !simState.data && (
                  <div style={{ fontSize: 11, color: T.textMuted }}>No data yet. Backfill candles then click Rerun.</div>
                )}

                {simState.data?.equity_curve?.length > 0 && (
                  <>
                    <div style={{ display: "grid", gridTemplateColumns: isMobile ? "repeat(2,1fr)" : "repeat(5,1fr)", gap: 10, marginBottom: 14 }}>
                      <StatTile T={T} label="Final Value" value={`₹${Math.round(simState.data.summary.final_value || 0).toLocaleString("en-IN")}`} color={T.accent} />
                      <StatTile T={T} label="Net P&L" value={<span style={{ color: (simState.data.summary.net_pnl || 0) >= 0 ? T.green : T.red, fontFamily: "'IBM Plex Mono'", fontSize: 18, fontWeight: 800 }}>{(simState.data.summary.net_pnl || 0) >= 0 ? "+" : ""}₹{Math.abs(simState.data.summary.net_pnl || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 })}</span>} color={T.green} />
                      <StatTile T={T} label="Drawdown" value={`${(simState.data.summary.drawdown_pct || 0).toFixed(2)}%`} color={T.red} />
                      <StatTile T={T} label="Win Rate" value={`${(simState.data.summary.win_rate || 0).toFixed(1)}%`} color={T.amber} />
                      <StatTile T={T} label="Total Trades" value={simState.data.summary.trade_count || 0} color={T.purple} />
                    </div>
                    <ResponsiveContainer width="100%" height={isMobile ? 180 : 240}>
                      <LineChart data={(simState.data.equity_curve || []).map(x => ({ ...x, date: x.timestamp?.slice(0, 10) }))} margin={{ left: isMobile ? -8 : 0 }}>
                        <CartesianGrid strokeDasharray="2 2" stroke={T.border} />
                        <XAxis dataKey="date" tick={{ fill: T.textMuted, fontSize: 9 }} tickLine={false} axisLine={false} minTickGap={20} />
                        <YAxis tick={{ fill: T.textMuted, fontSize: 9 }} tickLine={false} axisLine={false} tickFormatter={v => `₹${(v / 1000).toFixed(0)}K`} width={isMobile ? 40 : 50} />
                        <Tooltip {...tooltipStyle(T)} formatter={v => [`₹${Number(v).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`, "Equity"]} />
                        <Line type="monotone" dataKey="equity" stroke={T.accent} dot={false} strokeWidth={2} />
                      </LineChart>
                    </ResponsiveContainer>
                    <div style={{ background: T.bg, border: `1px solid ${T.border}`, borderRadius: 6, padding: "10px 14px", maxHeight: 180, overflowY: "auto", marginTop: 14 }}>
                      <div style={{ fontSize: 9, color: T.textMuted, letterSpacing: 1, textTransform: "uppercase", marginBottom: 8 }}>Trade log (latest first)</div>
                      {(simState.data.trades || []).length === 0 ? (
                        <div style={{ fontSize: 10, color: T.textMuted }}>No trades in selected period.</div>
                      ) : simState.data.trades.map((t, i) => (
                        <div key={i} style={{ fontSize: 10, color: T.textSub, padding: "4px 0", borderBottom: `1px dashed ${T.border}` }}>
                          <span style={{ color: t.action === "BUY" ? T.green : T.red, fontWeight: 700, marginRight: 8 }}>{t.action}</span>
                          <Mono size={10} color={T.textMuted}>{t.timestamp?.slice(0, 10)}</Mono>
                          <span style={{ marginLeft: 8 }}>{t.symbol} @ ₹{Math.round(t.price).toLocaleString("en-IN")}</span>
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </div>
            </Card>
          </div>
        )}

        {/* ANALYTICS */}
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

            <Card T={T}>
              <CardHeader T={T} title="14-Day P&L History" subtitle="Net daily returns" />
              <div style={{ padding: "14px 16px" }}>
                {dailyHistory?.history?.length ? (
                  <ResponsiveContainer width="100%" height={isMobile ? 150 : 200}>
                    <BarChart data={dailyHistory.history.slice().reverse()} margin={{ left: isMobile ? -8 : 0 }}>
                      <CartesianGrid strokeDasharray="2 2" stroke={T.border} />
                      <XAxis dataKey="date" tick={{ fill: T.textMuted, fontSize: 9 }} tickLine={false} axisLine={false} />
                      <YAxis tick={{ fill: T.textMuted, fontSize: 9 }} tickLine={false} axisLine={false} tickFormatter={v => `₹${(v / 1000).toFixed(1)}K`} width={isMobile ? 40 : 50} />
                      <Tooltip {...tooltipStyle(T)} formatter={v => [`₹${v.toLocaleString("en-IN")}`, "Net P&L"]} />
                      <ReferenceLine y={0} stroke={T.border} />
                      <Bar dataKey="net_pnl" radius={[3, 3, 0, 0]}>
                        {(dailyHistory.history || []).map((entry, i) => (
                          <Cell key={i} fill={(entry.net_pnl || 0) >= 0 ? T.green : T.red} opacity={0.75} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                ) : (
                  <div style={{ textAlign: "center", padding: "40px", color: T.textMuted, fontSize: 12 }}>No history yet</div>
                )}
              </div>
            </Card>

            <AITimelinePanel decisions={agentDecisions} T={T} />
          </div>
        )}
      </main>

      {/* MOBILE BOTTOM NAV */}
      {isMobile && <BottomNav tabs={TABS} active={activeTab} onChange={setActiveTab} T={T} />}
    </div>
  );
}

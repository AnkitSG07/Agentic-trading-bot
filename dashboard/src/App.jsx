import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import {
  AreaChart, Area, BarChart, Bar, LineChart, Line, ScatterChart, Scatter,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
  Cell, ComposedChart,
} from "recharts";
import {
  TrendingUp, TrendingDown, Activity, Shield, Zap,
  AlertTriangle, Power, RefreshCw, ArrowUpRight,
  ArrowDownRight, Target, DollarSign, ChevronUp, ChevronDown,
  Cpu, Radio, Database, BarChart2, Bell, Settings,
  Eye, Layers, GitBranch, Clock, Flame, TrendingUp as Trend,
  CheckCircle, XCircle, AlertCircle, Wifi, WifiOff,
  List, BarChart as BarChartIcon, ChevronRight, 
} from "lucide-react";

const isProduction = window.location.hostname !== "localhost";
const API_BASE = import.meta.env.VITE_API_BASE ??
  (isProduction
    ? "https://agentic-trading-bot-188e.onrender.com"
    : "http://localhost:8000");
const WS_URL = import.meta.env.VITE_WS_URL ??
  API_BASE.replace(/^http/, "ws") + "/ws";

// ─── HOOKS ────────────────────────────────────────────────────────────────────

function useAPI(endpoint, interval = null) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const fetch_ = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}${endpoint}`);
      if (!res.ok) { setData(null); setError(null); setLoading(false); return; }
      setData(await res.json()); setError(null);
    } catch (e) { setError(e.message); setData(null); }
    finally { setLoading(false); }
  }, [endpoint]);
  useEffect(() => {
    fetch_();
    if (interval) { const t = setInterval(fetch_, interval); return () => clearInterval(t); }
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
    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;
    ws.onopen = () => { setConnected(true); clearInterval(reconnectRef.current); };
    ws.onmessage = (e) => { try { setLiveData(JSON.parse(e.data)); } catch {} };
    ws.onclose = () => { setConnected(false); reconnectRef.current = setTimeout(connect, 3000); };
    ws.onerror = () => ws.close();
  }, []);
  useEffect(() => { connect(); return () => { clearInterval(reconnectRef.current); wsRef.current?.close(); }; }, [connect]);
  return { liveData, connected };
}

// ─── UI PRIMITIVES ────────────────────────────────────────────────────────────

const C = {
  bg: "#060b14", surface: "#0a1020", card: "#0d1526",
  border: "#1a2540", borderHover: "#243050",
  text: "#e2e8f0", textMuted: "#4a5568", textDim: "#2d3748",
  green: "#00d4a0", red: "#ff4757", amber: "#ffa502",
  blue: "#00b4d8", purple: "#7c3aed", cyan: "#22d3ee",
  greenDim: "#00d4a015", redDim: "#ff475715",
};

const tag = (text, color) => (
  <span style={{
    background: `${color}18`, color, border: `1px solid ${color}30`,
    borderRadius: 4, padding: "2px 7px", fontSize: 10, fontWeight: 700,
    letterSpacing: 0.6, textTransform: "uppercase", whiteSpace: "nowrap",
    fontFamily: "'JetBrains Mono', monospace",
  }}>{(text || "").replace(/_/g, " ")}</span>
);

const ACTION_COLORS = { BUY: C.green, SELL: C.red, SHORT: C.amber, COVER: C.blue, NO_ACTION: C.textMuted, HOLD: C.textMuted };
const STRATEGY_COLORS = { momentum: "#a855f7", mean_reversion: "#f97316", options_selling: "#ec4899", breakout: "#eab308", scalping: C.cyan, unknown: C.textMuted };
const REGIME_COLORS = { trending_up: C.green, trending_down: C.red, ranging: C.amber, high_volatility: "#ff6b6b" };

const Badge = ({ text, type }) => {
  const c = ACTION_COLORS[text] || STRATEGY_COLORS[text] || REGIME_COLORS[text] ||
    { complete: C.green, cancelled: C.textMuted, rejected: C.red, open: C.blue, pending: C.amber, zerodha: "#387ed1", dhan: "#00b386", risk_passed: C.green, risk_rejected: C.red }[
      (text || "").toLowerCase()] || C.textMuted;
  return tag(text, c);
};

const Num = ({ v, size = 14, prefix = "₹" }) => (
  <span style={{ color: v >= 0 ? C.green : C.red, fontFamily: "'JetBrains Mono', monospace", fontWeight: 700, fontSize: size }}>
    {v >= 0 ? "+" : ""}{prefix}{Math.abs(v).toLocaleString("en-IN", { maximumFractionDigits: 0 })}
  </span>
);

const Dot = ({ active, color = C.green }) => (
  <span style={{ display: "inline-block", width: 7, height: 7, borderRadius: "50%", background: active ? color : C.textMuted, boxShadow: active ? `0 0 6px ${color}` : "none" }} />
);

const Card = ({ children, style = {} }) => (
  <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, ...style }}>{children}</div>
);

const SectionHeader = ({ title, sub, right }) => (
  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "14px 18px", borderBottom: `1px solid ${C.border}` }}>
    <div>
      <div style={{ fontWeight: 700, fontSize: 12, letterSpacing: 0.3 }}>{title}</div>
      {sub && <div style={{ fontSize: 10, color: C.textMuted, marginTop: 2 }}>{sub}</div>}
    </div>
    {right}
  </div>
);

const MiniBar = ({ value, max, color }) => (
  <div style={{ width: "100%", height: 4, background: C.bg, borderRadius: 2, overflow: "hidden", marginTop: 4 }}>
    <div style={{ width: `${Math.min((value / max) * 100, 100)}%`, height: "100%", background: color, borderRadius: 2, transition: "width .4s" }} />
  </div>
);

const StatCard = ({ label, value, sub, color = C.cyan, icon: Icon, loading }) => (
  <Card>
    <div style={{ padding: "16px 18px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div style={{ flex: 1 }}>
          <div style={{ color: C.textMuted, fontSize: 9, letterSpacing: 2, textTransform: "uppercase", marginBottom: 8, fontFamily: "'JetBrains Mono', monospace" }}>{label}</div>
          {loading
            ? <div style={{ height: 24, background: C.border, borderRadius: 4, width: "55%", animation: "shimmer 1.5s infinite" }} />
            : <div style={{ color: C.text, fontSize: 20, fontWeight: 800, fontFamily: "'JetBrains Mono', monospace", letterSpacing: -1 }}>{value}</div>}
          {sub && <div style={{ fontSize: 10, color: C.textMuted, marginTop: 5 }}>{sub}</div>}
        </div>
        {Icon && <div style={{ background: `${color}12`, border: `1px solid ${color}20`, borderRadius: 8, padding: 9 }}><Icon size={15} color={color} /></div>}
      </div>
    </div>
  </Card>
);

const Sparkline = ({ data, color, height = 32 }) => {
  if (!data?.length) return <div style={{ height }} />;
  const min = Math.min(...data), max = Math.max(...data), range = max - min || 1;
  const w = 100, h = height;
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * w},${h - ((v - min) / range) * h * 0.85 - h * 0.075}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" width="100%" height={h} style={{ display: "block" }}>
      <polyline fill="none" stroke={color} strokeWidth="1.5" points={pts} strokeLinejoin="round" />
    </svg>
  );
};

// ─── PANELS ───────────────────────────────────────────────────────────────────

// 1. OPTIONS CHAIN PANEL
const OptionsChainPanel = ({ data }) => {
  const opts = data || {};
  const nifty = opts.NIFTY || {};
  const bnk = opts.BANKNIFTY || {};

  const pcr = Number(nifty.pcr);
  const hasPcr = Number.isFinite(pcr);
  const pcrColor = pcr > 1.2 ? C.green : pcr < 0.8 ? C.red : C.amber;
  const pcrLabel = pcr > 1.5 ? "EXTREME BULL" : pcr > 1.2 ? "BULLISH" : pcr > 0.8 ? "NEUTRAL" : pcr > 0.5 ? "BEARISH" : "EXTREME BEAR";

  const callOI = (nifty.top_call_oi || []).map((s, i) => ({ strike: s, oi: [4.2, 3.1, 2.8][i] || 1.5, type: "CE" }));
  const putOI = (nifty.top_put_oi || []).map((s, i) => ({ strike: s, oi: [3.8, 2.9, 2.1][i] || 1.2, type: "PE" }));
  const combined = [...putOI.reverse(), ...callOI].map(x => ({ ...x, label: `${x.strike}${x.type}` }));

  return (
    <Card>
      <SectionHeader title="Options Chain Intelligence" sub="Live PCR · Max Pain · OI Heatmap" right={
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span style={{ fontSize: 10, color: C.textMuted, fontFamily: "monospace" }}>PCR</span>
          <span style={{ fontSize: 14, fontWeight: 800, color: hasPcr ? pcrColor : C.textMuted, fontFamily: "monospace" }}>{hasPcr ? pcr.toFixed(2) : "—"}</span>
          {hasPcr ? tag(pcrLabel, pcrColor) : tag("NO DATA", C.textMuted)}
        </div>
      } />
      <div style={{ padding: "14px 18px", display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 16 }}>
        <div>
          <div style={{ fontSize: 10, color: C.textMuted, marginBottom: 8, letterSpacing: 1 }}>TOP OI CONCENTRATION — NIFTY</div>
          {combined.length === 0 ? (
            <div style={{ height: 160, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11, color: C.textMuted, background: C.bg, borderRadius: 6 }}>
              No live options OI data
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={160}>
              <BarChart data={combined} layout="vertical" margin={{ left: 60, right: 10 }}>
                <XAxis type="number" tick={{ fill: C.textMuted, fontSize: 9 }} tickLine={false} axisLine={false} />
                <YAxis type="category" dataKey="label" tick={{ fill: C.textMuted, fontSize: 9 }} tickLine={false} axisLine={false} width={55} />
                <Tooltip contentStyle={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 10 }}
                  formatter={(v, n, p) => [`${v.toFixed(1)}L OI`, p.payload.type === "CE" ? "Call" : "Put"]} />
                <Bar dataKey="oi" radius={[0, 3, 3, 0]}>
                  {combined.map((entry, i) => <Cell key={i} fill={entry.type === "CE" ? C.red : C.green} opacity={0.75} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {[
            { label: "ATM Strike", value: nifty.atm_strike?.toLocaleString("en-IN") || "—", color: C.cyan },
            { label: "Straddle Price", value: Number.isFinite(Number(nifty.atm_straddle)) ? `₹${nifty.atm_straddle}` : "—", color: C.purple },
            { label: "Expected Move", value: Number.isFinite(Number(nifty.expected_move_pct)) ? `±${Number(nifty.expected_move_pct).toFixed(2)}%` : "—", color: C.amber },
            { label: "Max Pain", value: nifty.max_pain?.toLocaleString("en-IN") || "—", color: C.amber },
            { label: "Key Resistance", value: nifty.key_resistance?.toLocaleString("en-IN") || "—", color: C.red },
            { label: "Key Support", value: nifty.key_support?.toLocaleString("en-IN") || "—", color: C.green },
          ].map(row => (
            <div key={row.label} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "7px 10px", background: C.bg, borderRadius: 6 }}>
              <span style={{ fontSize: 10, color: C.textMuted }}>{row.label}</span>
              <span style={{ fontSize: 11, fontWeight: 700, color: row.color, fontFamily: "monospace" }}>{row.value}</span>
            </div>
          ))}
          <div style={{ display: "flex", justifyContent: "space-between", padding: "7px 10px", background: C.bg, borderRadius: 6 }}>
            <span style={{ fontSize: 10, color: C.textMuted }}>BANKNIFTY PCR</span>
            <span style={{ fontSize: 11, fontWeight: 700, fontFamily: "monospace", color: Number.isFinite(Number(bnk.pcr)) ? (Number(bnk.pcr) > 1 ? C.green : C.red) : C.textMuted }}>{Number.isFinite(Number(bnk.pcr)) ? Number(bnk.pcr).toFixed(2) : "—"}</span>
          </div>
        </div>
      </div>
    </Card>
  );
};

// 2. WATCHLIST INDICATORS PANEL
const SIGNAL_COLORS = { strong_buy: C.green, buy: "#4ade80", neutral: C.amber, sell: "#f87171", strong_sell: C.red };
const SIGNAL_LABELS = { strong_buy: "STRONG BUY", buy: "BUY", neutral: "NEUTRAL", sell: "SELL", strong_sell: "STRONG SELL" };

const WatchlistIndicatorsPanel = ({ watchlistData }) => {
  const data = watchlistData?.length ? watchlistData : [];
  const [selected, setSelected] = useState(null);

  return (
    <Card>
      <SectionHeader title="Watchlist Intelligence" sub="Real-time indicator matrix — what the AI sees" right={
        <span style={{ fontSize: 10, color: C.textMuted }}>{data.length} symbols</span>
      } />
      <div style={{ padding: "0 18px 14px" }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 1, marginTop: 12 }}>
          <div style={{ display: "grid", gridTemplateColumns: "100px 70px 55px 60px 65px 65px 65px 80px", gap: 4, padding: "4px 0 8px", marginBottom: 4, borderBottom: `1px solid ${C.border}` }}>
            {["SYMBOL", "LTP", "CHG%", "RSI", "MACD", "BB", "VOL", "SIGNAL"].map(h =>
              <span key={h} style={{ fontSize: 9, color: C.textMuted, letterSpacing: 1.2, textTransform: "uppercase", fontFamily: "monospace" }}>{h}</span>
            )}
          </div>
          {data.map((w, i) => {
            const ind = w.indicators || {};
            const sig = ind.overall_signal || "neutral";
            const sigColor = SIGNAL_COLORS[sig] || C.textMuted;
            const rsi = Number(ind.rsi || 50);
            const rsiColor = rsi > 70 ? C.red : rsi < 30 ? C.green : C.text;
            const isSelected = selected === w.symbol;
            return (
              <div key={w.symbol}>
                <div
                  onClick={() => setSelected(isSelected ? null : w.symbol)}
                  style={{
                    display: "grid", gridTemplateColumns: "100px 70px 55px 60px 65px 65px 65px 80px", gap: 4,
                    padding: "9px 6px", borderRadius: 6, cursor: "pointer",
                    background: isSelected ? `${C.cyan}08` : i % 2 === 0 ? C.bg : "transparent",
                    transition: "background .15s",
                  }}
                >
                  <span style={{ fontSize: 11, fontWeight: 700 }}>{w.symbol}</span>
                  <span style={{ fontSize: 11, fontFamily: "monospace", color: C.text }}>₹{(w.ltp || 0).toFixed(0)}</span>
                  <span style={{ fontSize: 11, fontFamily: "monospace", color: (w.change_pct || 0) >= 0 ? C.green : C.red }}>
                    {(w.change_pct || 0) >= 0 ? "+" : ""}{(w.change_pct || 0).toFixed(2)}%
                  </span>
                  <span style={{ fontSize: 11, fontFamily: "monospace", color: rsiColor }}>{rsi}</span>
                  <span style={{ fontSize: 10 }}>{tag(ind.macd_signal || "neutral", ind.macd_signal === "bullish" ? C.green : ind.macd_signal === "bearish" ? C.red : C.amber)}</span>
                  <span style={{ fontSize: 10 }}>{tag(ind.bb_signal || "middle", ind.bb_signal === "upper" ? C.amber : ind.bb_signal === "lower" ? C.blue : C.textMuted)}</span>
                  <span style={{ fontSize: 11, fontFamily: "monospace", color: (ind.volume_ratio || 1) > 1.5 ? C.amber : C.textMuted }}>{(ind.volume_ratio || 1).toFixed(1)}x</span>
                  <span style={{ fontSize: 9, fontWeight: 800, color: sigColor }}>{SIGNAL_LABELS[sig] || sig.toUpperCase()}</span>
                </div>
                {isSelected && (
                  <div style={{ padding: "10px 8px 12px", background: `${C.cyan}06`, borderRadius: 6, marginBottom: 4 }}>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 8 }}>
                      {[
                        { label: "Pivot", v: w.levels?.pivot, color: C.textMuted },
                        { label: "R1", v: w.levels?.r1, color: C.red },
                        { label: "S1", v: w.levels?.s1, color: C.green },
                        { label: "Supertrend", v: ind.supertrend || "—", color: ind.supertrend === "bullish" ? C.green : C.red, isText: true },
                      ].map(item => (
                        <div key={item.label} style={{ background: C.surface, borderRadius: 6, padding: "8px 10px" }}>
                          <div style={{ fontSize: 9, color: C.textMuted, marginBottom: 3 }}>{item.label}</div>
                          <div style={{ fontSize: 11, fontWeight: 700, fontFamily: "monospace", color: item.color }}>
                            {item.isText ? item.v : item.v ? `₹${item.v}` : "—"}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
          {data.length === 0 && <div style={{ fontSize: 11, color: C.textMuted, paddingTop: 8 }}>No live watchlist data</div>}
        </div>
      </div>
    </Card>
  );
};

// 3. POSITION SPARKLINES WITH SL DISTANCE
const PositionSparklinePanel = ({ positions, tickHistory, prevTicks }) => {
  if (!positions?.length) return (
    <Card>
      <SectionHeader title="Open Positions" sub="Live P&L with sparklines" />
      <div style={{ padding: "40px 18px", textAlign: "center", color: C.textMuted, fontSize: 12 }}>No open positions</div>
    </Card>
  );

  return (
    <Card>
      <SectionHeader title="Open Positions" sub="Live P&L with price sparklines" right={
        <span style={{ fontSize: 11, fontWeight: 700, color: C.text }}>{positions.length} open</span>
      } />
      <div style={{ padding: "0 0 8px" }}>
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
            <div key={i} style={{ borderBottom: `1px solid ${C.border}`, padding: "12px 18px", display: "grid", gridTemplateColumns: "90px 50px 80px 90px 100px 80px 1fr", alignItems: "center", gap: 12 }}>
              <div>
                <div style={{ fontWeight: 800, fontSize: 12 }}>{p.symbol}</div>
                <div style={{ marginTop: 3 }}>{tag(p.side, p.side === "BUY" ? C.green : C.red)}</div>
              </div>
              <div style={{ fontFamily: "monospace", fontSize: 11, color: C.textMuted }}>{qty}</div>
              <div>
                <div style={{ fontSize: 9, color: C.textMuted, marginBottom: 2 }}>AVG / LTP</div>
                <div style={{ fontFamily: "monospace", fontSize: 10 }}>₹{avg.toFixed(0)} / <span style={{ color: isUp ? C.green : C.red }}>₹{ltp.toFixed(0)}</span></div>
              </div>
              <div>
                <Num v={pnl} size={13} />
                <div style={{ fontSize: 10, color: isUp ? C.green : C.red }}>{pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(2)}%</div>
              </div>
              <div>
                {slDist !== null ? (
                  <>
                    <div style={{ fontSize: 9, color: C.textMuted, marginBottom: 3 }}>SL Distance</div>
                    <div style={{ fontFamily: "monospace", fontSize: 11, color: slDist < 0.5 ? C.red : slDist < 1.5 ? C.amber : C.green }}>{slDist.toFixed(2)}%</div>
                    <MiniBar value={Math.min(slDist, 5)} max={5} color={slDist < 1 ? C.red : slDist < 2 ? C.amber : C.green} />
                  </>
                ) : <span style={{ fontSize: 10, color: C.textMuted }}>No SL</span>}
              </div>
              <div>
                {p.target ? (
                  <>
                    <div style={{ fontSize: 9, color: C.textMuted, marginBottom: 3 }}>Target</div>
                    <div style={{ fontFamily: "monospace", fontSize: 11, color: C.cyan }}>₹{Number(p.target).toFixed(0)}</div>
                  </>
                ) : <span style={{ fontSize: 10, color: C.textMuted }}>—</span>}
              </div>
              <div style={{ width: "100%", opacity: 0.85 }}>
                <Sparkline data={series.length ? series : [avg, ltp]} color={isUp ? C.green : C.red} height={38} />
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
};

// 4. AI CONFIDENCE TIMELINE
const ConfidenceTimelinePanel = ({ decisions }) => {
  const chartData = useMemo(() => {
    const all = decisions || [];
    return all.slice(-30).map((d, i) => ({
      time: d.timestamp ? new Date(d.timestamp).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }) : `T-${all.length - i}`,
      confidence: Math.round(((d.signals || [])[0]?.confidence || 0.5) * 100),
      signals: d.signals_generated || 0,
      latency: d.latency_ms || 0,
      regime: d.market_regime || "unknown",
    }));
  }, [decisions]);

  if (!chartData.length) return (
    <Card>
      <SectionHeader title="AI Confidence Timeline" sub="Signal quality over time" />
      <div style={{ padding: "40px 18px", textAlign: "center", color: C.textMuted, fontSize: 12 }}>No decision history yet</div>
    </Card>
  );

  return (
    <Card>
      <SectionHeader title="AI Confidence Timeline" sub="Signal confidence & latency per cycle" />
      <div style={{ padding: "14px 18px" }}>
        <ResponsiveContainer width="100%" height={180}>
          <ComposedChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" stroke={C.border} />
            <XAxis dataKey="time" tick={{ fill: C.textMuted, fontSize: 9 }} tickLine={false} axisLine={false} interval={4} />
            <YAxis yAxisId="conf" domain={[0, 100]} tick={{ fill: C.textMuted, fontSize: 9 }} tickLine={false} axisLine={false} tickFormatter={v => `${v}%`} />
            <YAxis yAxisId="lat" orientation="right" tick={{ fill: C.textMuted, fontSize: 9 }} tickLine={false} axisLine={false} tickFormatter={v => `${v}ms`} />
            <Tooltip contentStyle={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 10 }}
              formatter={(v, n) => [n === "confidence" ? `${v}%` : n === "latency" ? `${v}ms` : v, n]} />
            <ReferenceLine yAxisId="conf" y={65} stroke={C.amber} strokeDasharray="4 2" strokeWidth={1} />
            <Bar yAxisId="conf" dataKey="confidence" fill={C.cyan} opacity={0.2} radius={[2, 2, 0, 0]} />
            <Line yAxisId="conf" type="monotone" dataKey="confidence" stroke={C.cyan} strokeWidth={2} dot={false} />
            <Line yAxisId="lat" type="monotone" dataKey="latency" stroke={C.purple} strokeWidth={1.5} dot={false} strokeDasharray="4 2" />
          </ComposedChart>
        </ResponsiveContainer>
        <div style={{ display: "flex", gap: 16, marginTop: 8 }}>
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}><span style={{ width: 12, height: 2, background: C.cyan, display: "inline-block" }} /><span style={{ fontSize: 10, color: C.textMuted }}>Confidence</span></div>
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}><span style={{ width: 12, height: 2, background: C.purple, display: "inline-block" }} /><span style={{ fontSize: 10, color: C.textMuted }}>Latency</span></div>
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}><span style={{ width: 12, height: 2, background: C.amber, borderStyle: "dashed", borderWidth: 1, display: "inline-block" }} /><span style={{ fontSize: 10, color: C.textMuted }}>65% threshold</span></div>
        </div>
      </div>
    </Card>
  );
};

// 5. STRATEGY REVIEW PANEL
const StrategyReviewPanel = ({ reviewData }) => {
  const review = reviewData || {
    strategy_weights: { momentum: 0.30, mean_reversion: 0.25, options_selling: 0.30, breakout: 0.15 },
    avoid_patterns: ["Avoid chasing breakouts in first 30 minutes", "Skip signals when VIX > 22"],
    focus_patterns: ["High-volume MACD crossovers between 10:30–2:00", "Mean reversion at BB extremes with RSI confirmation"],
    overall_assessment: "Momentum and options selling strategies are performing best. Mean reversion needs tighter stops.",
    parameter_adjustments: { rsi_overbought: 72, confidence_threshold: 0.70 },
  };

  const weights = review.strategy_weights || {};
  const maxWeight = Math.max(...Object.values(weights));

  return (
    <Card>
      <SectionHeader title="AI Strategy Review" sub="Latest performance review & parameter adjustments" right={
        tag("auto-updated", C.green)
      } />
      <div style={{ padding: "14px 18px", display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <div>
          <div style={{ fontSize: 10, color: C.textMuted, letterSpacing: 1, marginBottom: 10 }}>STRATEGY WEIGHTS</div>
          {Object.entries(weights).map(([strategy, weight]) => (
            <div key={strategy} style={{ marginBottom: 10 }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                <span style={{ fontSize: 11, color: C.text, textTransform: "capitalize" }}>{strategy.replace(/_/g, " ")}</span>
                <span style={{ fontSize: 11, fontFamily: "monospace", color: STRATEGY_COLORS[strategy] || C.cyan }}>{(weight * 100).toFixed(0)}%</span>
              </div>
              <MiniBar value={weight} max={maxWeight} color={STRATEGY_COLORS[strategy] || C.cyan} />
            </div>
          ))}

          <div style={{ marginTop: 14, padding: "10px 12px", background: C.bg, borderRadius: 6 }}>
            <div style={{ fontSize: 10, color: C.textMuted, marginBottom: 8, letterSpacing: 1 }}>PARAMETER ADJUSTMENTS</div>
            {Object.entries(review.parameter_adjustments || {}).map(([k, v]) => (
              <div key={k} style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                <span style={{ fontSize: 10, color: C.textMuted }}>{k.replace(/_/g, " ")}</span>
                <span style={{ fontSize: 11, fontFamily: "monospace", color: C.cyan }}>{v}</span>
              </div>
            ))}
          </div>
        </div>

        <div>
          <div style={{ fontSize: 10, color: C.textMuted, letterSpacing: 1, marginBottom: 8 }}>ASSESSMENT</div>
          <div style={{ fontSize: 11, color: C.text, lineHeight: 1.7, padding: "10px 12px", background: C.bg, borderRadius: 6, marginBottom: 12 }}>
            {review.overall_assessment}
          </div>

          <div style={{ marginBottom: 10 }}>
            <div style={{ fontSize: 10, color: C.green, letterSpacing: 1, marginBottom: 6, display: "flex", alignItems: "center", gap: 5 }}>
              <CheckCircle size={10} /> FOCUS PATTERNS
            </div>
            {(review.focus_patterns || []).map((p, i) => (
              <div key={i} style={{ fontSize: 10, color: C.textMuted, padding: "5px 10px", background: `${C.green}08`, borderLeft: `2px solid ${C.green}40`, borderRadius: 3, marginBottom: 4 }}>{p}</div>
            ))}
          </div>

          <div>
            <div style={{ fontSize: 10, color: C.red, letterSpacing: 1, marginBottom: 6, display: "flex", alignItems: "center", gap: 5 }}>
              <XCircle size={10} /> AVOID PATTERNS
            </div>
            {(review.avoid_patterns || []).map((p, i) => (
              <div key={i} style={{ fontSize: 10, color: C.textMuted, padding: "5px 10px", background: `${C.red}08`, borderLeft: `2px solid ${C.red}40`, borderRadius: 3, marginBottom: 4 }}>{p}</div>
            ))}
          </div>
        </div>
      </div>
    </Card>
  );
};

// 6. MODEL FALLBACK INDICATOR
const ModelFallbackPanel = ({ decisions }) => {
  const stats = useMemo(() => {
    const all = decisions || [];
    const counts = {};
    const latencies = {};
    all.forEach(d => {
      const m = d.model_used || "unknown";
      counts[m] = (counts[m] || 0) + 1;
      if (!latencies[m]) latencies[m] = [];
      if (d.latency_ms) latencies[m].push(d.latency_ms);
    });
    return Object.entries(counts).map(([model, count]) => ({
      model: model.replace("gemini-", "").replace("-preview", "").replace("-latest", ""),
      fullModel: model,
      count,
      pct: all.length ? Math.round((count / all.length) * 100) : 0,
      avgLatency: latencies[model]?.length ? Math.round(latencies[model].reduce((a, b) => a + b, 0) / latencies[model].length) : 0,
      isFallback: model !== (all[0]?.model_requested || model),
    })).sort((a, b) => b.count - a.count);
  }, [decisions]);

  const primary = stats[0];
  const fallbacks = stats.slice(1);
  const fallbackRate = stats.length > 1 ? fallbacks.reduce((s, x) => s + x.pct, 0) : 0;

  return (
    <Card>
      <SectionHeader title="Model Performance" sub="Gemini model usage & fallback tracking" right={
        fallbackRate > 0 ? tag(`${fallbackRate}% fallback`, C.amber) : tag("100% primary", C.green)
      } />
      <div style={{ padding: "14px 18px" }}>
        {stats.length === 0 ? (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 10 }}>
            {[
              { model: "2.5-flash", pct: 82, isFallback: false, avgLatency: 1240 },
              { model: "2.0-flash", pct: 12, isFallback: true, avgLatency: 890 },
              { model: "2.5-flash-lite", pct: 4, isFallback: true, avgLatency: 640 },
              { model: "2.0-flash-lite", pct: 2, isFallback: true, avgLatency: 410 },
            ].map(m => (
              <div key={m.model} style={{ background: C.bg, borderRadius: 8, padding: "12px 14px", border: `1px solid ${m.isFallback ? C.amber + "30" : C.green + "30"}` }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                  <span style={{ fontSize: 11, fontWeight: 700, fontFamily: "monospace" }}>gemini-{m.model}</span>
                  {tag(m.isFallback ? "fallback" : "primary", m.isFallback ? C.amber : C.green)}
                </div>
                <MiniBar value={m.pct} max={100} color={m.isFallback ? C.amber : C.green} />
                <div style={{ display: "flex", justifyContent: "space-between", marginTop: 6 }}>
                  <span style={{ fontSize: 10, color: C.textMuted }}>{m.pct}% of calls</span>
                  <span style={{ fontSize: 10, color: C.textMuted }}>{m.avgLatency}ms avg</span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 10 }}>
            {stats.map(m => (
              <div key={m.model} style={{ background: C.bg, borderRadius: 8, padding: "12px 14px", border: `1px solid ${m.isFallback ? C.amber + "30" : C.green + "30"}` }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                  <span style={{ fontSize: 11, fontWeight: 700, fontFamily: "monospace" }}>gemini-{m.model}</span>
                  {tag(m.isFallback ? "fallback" : "primary", m.isFallback ? C.amber : C.green)}
                </div>
                <MiniBar value={m.pct} max={100} color={m.isFallback ? C.amber : C.green} />
                <div style={{ display: "flex", justifyContent: "space-between", marginTop: 6 }}>
                  <span style={{ fontSize: 10, color: C.textMuted }}>{m.pct}% of calls</span>
                  <span style={{ fontSize: 10, color: C.textMuted }}>{m.avgLatency}ms avg</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
};

// 7. KILL SWITCH HISTORY
const KillSwitchHistoryPanel = ({ risk, riskEvents }) => {
  const daily_pnl_pct = risk?.daily_pnl_pct || 0;
  const drawdown = risk?.drawdown_pct || 0;
  const killSwitch = risk?.kill_switch;
  const dailyLossLimit = 2.0;
  const drawdownLimit = 8.0;
  const events = (riskEvents?.events || []).filter(e => e.type?.includes("KILL") || e.type?.includes("STOP") || e.severity === "CRITICAL");

  const gaugeData = [
    { label: "Daily Loss", value: Math.abs(Math.min(0, daily_pnl_pct)), max: dailyLossLimit, color: C.red },
    { label: "Drawdown", value: drawdown, max: drawdownLimit, color: C.amber },
  ];

  return (
    <Card>
      <SectionHeader title="Kill Switch Monitor" sub="Live safety gauges & trigger history" right={
        killSwitch ? tag("TRIGGERED", C.red) : tag("SAFE", C.green)
      } />
      <div style={{ padding: "14px 18px" }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 12, marginBottom: 14 }}>
          {gaugeData.map(g => {
            const pct = Math.min((g.value / g.max) * 100, 100);
            return (
              <div key={g.label} style={{ background: C.bg, borderRadius: 8, padding: "12px 14px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                  <span style={{ fontSize: 10, color: C.textMuted }}>{g.label}</span>
                  <span style={{ fontSize: 11, fontFamily: "monospace", color: pct > 75 ? g.color : C.text }}>{g.value.toFixed(2)}% / {g.max}%</span>
                </div>
                <div style={{ position: "relative", height: 8, background: C.border, borderRadius: 4 }}>
                  <div style={{ position: "absolute", width: `${pct}%`, height: "100%", background: `linear-gradient(90deg, ${g.color}88, ${g.color})`, borderRadius: 4, transition: "width .4s", boxShadow: pct > 75 ? `0 0 8px ${g.color}` : "none" }} />
                  <div style={{ position: "absolute", left: "75%", top: 0, height: "100%", width: 1, background: C.border }} />
                </div>
                <div style={{ fontSize: 9, color: C.textMuted, marginTop: 4 }}>Trigger at {g.max}% — {(g.max - g.value).toFixed(2)}% remaining</div>
              </div>
            );
          })}
        </div>

        <div style={{ fontSize: 10, color: C.textMuted, letterSpacing: 1, marginBottom: 8 }}>RECENT RISK EVENTS</div>
        {events.length === 0 ? (
          <div style={{ fontSize: 11, color: C.textMuted, padding: "12px 0" }}>No critical events today — system operating normally</div>
        ) : events.slice(0, 5).map((e, i) => (
          <div key={i} style={{ display: "flex", gap: 10, alignItems: "center", padding: "8px 10px", background: C.bg, borderRadius: 6, marginBottom: 4, borderLeft: `2px solid ${C.red}` }}>
            <AlertTriangle size={11} color={C.red} />
            <span style={{ fontSize: 10, fontFamily: "monospace", color: C.textMuted }}>{e.timestamp ? new Date(e.timestamp).toLocaleTimeString("en-IN") : "—"}</span>
            <span style={{ fontSize: 11, color: C.text, flex: 1 }}>{e.description || e.message}</span>
            {tag(e.severity || "CRITICAL", C.red)}
          </div>
        ))}
      </div>
    </Card>
  );
};

// 8. ORDER EXECUTION QUEUE
const ExecutionQueuePanel = ({ orders }) => {
  const pending = (orders || []).filter(o => ["PENDING", "OPEN", "TRIGGER PENDING"].includes((o.status || "").toUpperCase()));
  const recent = (orders || []).filter(o => ["COMPLETE", "FILLED"].includes((o.status || "").toUpperCase())).slice(0, 8);
  const displayPending = pending;

  return (
    <Card>
      <SectionHeader title="Execution Queue" sub="Pending orders & recent fills" right={
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {displayPending.length > 0 && <span style={{ fontSize: 11, color: C.amber, fontFamily: "monospace" }}>{displayPending.length} pending</span>}
          {tag("live", C.green)}
        </div>
      } />
      <div style={{ padding: "10px 18px 14px" }}>
        {displayPending.length > 0 && (
          <div style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 10, color: C.amber, letterSpacing: 1, marginBottom: 6 }}>PENDING ORDERS</div>
            {displayPending.map((o, i) => {
              const age = o.placed_at ? Math.round((Date.now() - new Date(o.placed_at).getTime()) / 1000) : 0;
              return (
                <div key={i} style={{ display: "flex", gap: 10, alignItems: "center", padding: "9px 12px", background: C.bg, borderRadius: 6, marginBottom: 4, border: `1px solid ${C.amber}20` }}>
                  <div style={{ width: 6, height: 6, borderRadius: "50%", background: C.amber, animation: "pulseDot 1.5s infinite" }} />
                  <span style={{ fontWeight: 700, fontSize: 11, minWidth: 70 }}>{o.symbol}</span>
                  {tag(o.side, o.side === "BUY" ? C.green : C.red)}
                  <span style={{ fontFamily: "monospace", fontSize: 10, color: C.textMuted }}>{o.quantity} @ {o.price ? `₹${o.price}` : `₹${o.trigger_price} TRIGGER`}</span>
                  {tag(o.order_type || "LIMIT", C.blue)}
                  <span style={{ marginLeft: "auto", fontSize: 10, color: C.textMuted }}>{age}s ago</span>
                  {tag(o.tag || "—", C.textMuted)}
                </div>
              );
            })}
          </div>
        )}

        <div style={{ fontSize: 10, color: C.textMuted, letterSpacing: 1, marginBottom: 6 }}>RECENT FILLS</div>
        {(recent.length ? recent : []).slice(0, 5).map((o, i) => {
          const slippage = o.price && o.average_price ? ((o.average_price - o.price) / o.price * 100) : null;
          return (
            <div key={i} style={{ display: "flex", gap: 10, alignItems: "center", padding: "8px 12px", background: C.bg, borderRadius: 6, marginBottom: 3 }}>
              <CheckCircle size={11} color={C.green} />
              <span style={{ fontWeight: 700, fontSize: 11, minWidth: 70 }}>{o.symbol}</span>
              {tag(o.side, o.side === "BUY" ? C.green : C.red)}
              <span style={{ fontFamily: "monospace", fontSize: 10, color: C.textMuted }}>{o.quantity}</span>
              <span style={{ fontFamily: "monospace", fontSize: 10, color: C.text }}>
                ₹{o.average_price?.toLocaleString("en-IN") || o.price?.toLocaleString("en-IN") || "MKT"}
              </span>
              {slippage !== null && (
                <span style={{ fontSize: 10, color: Math.abs(slippage) > 0.1 ? C.amber : C.green, fontFamily: "monospace" }}>
                  {slippage >= 0 ? "+" : ""}{slippage.toFixed(3)}% slip
                </span>
              )}
              <span style={{ marginLeft: "auto", fontSize: 9, color: C.textMuted }}>
                {o.placed_at ? new Date(o.placed_at).toLocaleTimeString("en-IN") : "—"}
              </span>
            </div>
          );
        })}
        {recent.length === 0 && (
          <div style={{ fontSize: 11, color: C.textMuted, paddingTop: 4 }}>No fills yet today</div>
        )}
      </div>
    </Card>
  );
};

// 9. SL ORDER STATUS PANEL
const SLOrderStatusPanel = ({ positions, ticks }) => {
  const items = useMemo(() => {
    return (positions || []).map(p => {
      const ltp = Number(ticks?.[p.symbol] || p.ltp || 0);
      const sl = Number(p.stop_loss || p.current_sl || 0);
      const entry = Number(p.avg || p.entry_price || 0);
      const target = Number(p.target || 0);
      const side = (p.side || "BUY").toUpperCase();
      const slDist = sl && ltp ? Math.abs(((ltp - sl) / ltp) * 100) : null;
      const targetDist = target && ltp ? Math.abs(((target - ltp) / ltp) * 100) : null;
      const progressToTarget = (entry && target && ltp && entry !== target)
        ? Math.max(0, Math.min(100, ((ltp - entry) / (target - entry)) * 100))
        : 0;
      return { ...p, ltp, sl, entry, target, slDist, targetDist, progressToTarget, side };
    });
  }, [positions, ticks]);
  
  const display = items;

  return (
    <Card>
      <SectionHeader title="SL / Target Tracker" sub="Stop-loss status, trailing stops, target proximity" />
      <div style={{ padding: "10px 18px 14px" }}>
        {display.map((p, i) => (
          <div key={i} style={{ marginBottom: 14, padding: "12px 14px", background: C.bg, borderRadius: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <span style={{ fontWeight: 800, fontSize: 12 }}>{p.symbol}</span>
                {tag(p.side, p.side === "BUY" ? C.green : C.red)}
              </div>
              <span style={{ fontFamily: "monospace", fontSize: 12, fontWeight: 700 }}>₹{p.ltp.toFixed(0)}</span>
            </div>

            <div style={{ position: "relative", height: 14, background: C.border, borderRadius: 7, marginBottom: 8, overflow: "visible" }}>
              <div style={{ position: "absolute", left: 0, width: `${p.progressToTarget}%`, height: "100%", background: `linear-gradient(90deg, ${C.green}60, ${C.green})`, borderRadius: 7, transition: "width .5s" }} />
              {/* SL marker */}
              <div style={{ position: "absolute", left: "5%", top: -3, height: 20, width: 2, background: C.red }} title={`SL: ₹${p.sl}`} />
              {/* Target marker */}
              <div style={{ position: "absolute", right: "5%", top: -3, height: 20, width: 2, background: C.green }} title={`Target: ₹${p.target}`} />
              {/* LTP marker */}
              <div style={{ position: "absolute", left: `${p.progressToTarget}%`, top: -5, transform: "translateX(-50%)", width: 10, height: 10, background: C.cyan, borderRadius: "50%", border: "2px solid #060b14", boxShadow: `0 0 6px ${C.cyan}` }} />
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 8 }}>
              {[
                { label: "Entry", value: `₹${p.entry.toFixed(0)}`, color: C.textMuted },
                { label: "Stop Loss", value: p.sl ? `₹${p.sl.toFixed(0)}` : "—", color: C.red, sub: p.slDist ? `${p.slDist.toFixed(1)}% away` : null },
                { label: "Target", value: p.target ? `₹${p.target.toFixed(0)}` : "—", color: C.green, sub: p.targetDist ? `${p.targetDist.toFixed(1)}% away` : null },
                { label: "Progress", value: `${p.progressToTarget.toFixed(0)}%`, color: p.progressToTarget > 50 ? C.green : C.amber },
              ].map(item => (
                <div key={item.label}>
                  <div style={{ fontSize: 9, color: C.textMuted, marginBottom: 3 }}>{item.label}</div>
                  <div style={{ fontSize: 11, fontWeight: 700, fontFamily: "monospace", color: item.color }}>{item.value}</div>
                  {item.sub && <div style={{ fontSize: 9, color: C.textMuted }}>{item.sub}</div>}
                </div>
              ))}
            </div>
          </div>
        ))}
        {display.length === 0 && <div style={{ fontSize: 12, color: C.textMuted, padding: "20px 0" }}>No tracked positions</div>}
      </div>
    </Card>
  );
};

// 10. INTRADAY CANDLESTICK CHART (simple OHLCV bar simulation)
const IntradayChartPanel = ({ ticks, tickHistory }) => {
  const [symbol, setSymbol] = useState("NIFTY");
  const symbols = ["NIFTY", "BANKNIFTY", "RELIANCE", "HDFCBANK", "TCS", "SBIN"];

  const chartData = useMemo(() => {
    const series = tickHistory?.[symbol] || [];
    if (series.length < 2) return [];
    return series.map((v, i) => ({
      time: `T-${series.length - i}`, open: v * 0.999, close: v, high: v * 1.001, low: v * 0.998, volume: Math.round(Math.random() * 50000),
    }));
  }, [symbol, tickHistory]);

  const lastBar = chartData[chartData.length - 1];
  const firstBar = chartData[0];
  const dayChange = lastBar && firstBar ? ((lastBar.close - firstBar.open) / firstBar.open) * 100 : 0;

  return (
    <Card>
      <SectionHeader
        title="Intraday Chart"
        sub="15-min OHLCV with volume"
        right={
          <div style={{ display: "flex", gap: 6 }}>
            {symbols.map(s => (
              <button key={s} onClick={() => setSymbol(s)} style={{
                background: symbol === s ? `${C.cyan}20` : "transparent",
                border: `1px solid ${symbol === s ? C.cyan : C.border}`,
                borderRadius: 4, padding: "3px 8px", cursor: "pointer",
                fontSize: 9, color: symbol === s ? C.cyan : C.textMuted, fontFamily: "monospace",
              }}>{s}</button>
            ))}
          </div>
        }
      />
      <div style={{ padding: "10px 18px 14px" }}>
        <div style={{ display: "flex", gap: 20, marginBottom: 12 }}>
          {lastBar && [
            { label: "Open", value: firstBar?.open.toFixed(2) },
            { label: "High", value: Math.max(...chartData.map(d => d.high)).toFixed(2), color: C.green },
            { label: "Low", value: Math.min(...chartData.map(d => d.low)).toFixed(2), color: C.red },
            { label: "Last", value: lastBar.close.toFixed(2), color: dayChange >= 0 ? C.green : C.red },
            { label: "Change", value: `${dayChange >= 0 ? "+" : ""}${dayChange.toFixed(2)}%`, color: dayChange >= 0 ? C.green : C.red },
          ].map(item => (
            <div key={item.label}>
              <div style={{ fontSize: 9, color: C.textMuted }}>{item.label}</div>
              <div style={{ fontSize: 12, fontFamily: "monospace", fontWeight: 700, color: item.color || C.text }}>
                {item.value}
              </div>
            </div>
          ))}
        </div>

        {chartData.length === 0 ? (
          <div style={{ height: 200, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11, color: C.textMuted, background: C.bg, borderRadius: 6 }}>
            No live intraday ticks for {symbol}
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <ComposedChart data={chartData} margin={{ top: 5, right: 5, bottom: 0, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={C.border} />
              <XAxis dataKey="time" tick={{ fill: C.textMuted, fontSize: 8 }} tickLine={false} axisLine={false} interval={8} />
              <YAxis yAxisId="price" domain={["auto", "auto"]} tick={{ fill: C.textMuted, fontSize: 9 }} tickLine={false} axisLine={false} tickFormatter={v => v.toFixed(0)} />
              <YAxis yAxisId="vol" orientation="right" tick={{ fill: C.textMuted, fontSize: 8 }} tickLine={false} axisLine={false} tickFormatter={v => `${(v / 1000).toFixed(0)}K`} />
              <Tooltip contentStyle={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 10 }}
                formatter={(v, name) => [name === "volume" ? `${(v / 1000).toFixed(1)}K` : v.toFixed(2), name]} />
              <Bar yAxisId="vol" dataKey="volume" fill={C.purple} opacity={0.25} radius={[1, 1, 0, 0]} />
              <Line yAxisId="price" type="monotone" dataKey="close" stroke={dayChange >= 0 ? C.green : C.red} strokeWidth={2} dot={false} />
              <Line yAxisId="price" type="monotone" dataKey="high" stroke={C.green} strokeWidth={0.5} dot={false} strokeDasharray="2 2" />
              <Line yAxisId="price" type="monotone" dataKey="low" stroke={C.red} strokeWidth={0.5} dot={false} strokeDasharray="2 2" />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </div>
    </Card>
  );
};

// ─── MAIN DASHBOARD ───────────────────────────────────────────────────────────

export default function TradingDashboard() {
  const { liveData, connected } = useWebSocket();
  const [activeTab, setActiveTab] = useState("overview");
  const [pnlHistory, setPnlHistory] = useState([]);
  const [tickHistory, setTickHistory] = useState({});
  const [prevTicks, setPrevTicks] = useState({});
  const [eventTape, setEventTape] = useState([]);
  const [lastUpdate, setLastUpdate] = useState(new Date());
  const [engineRunning, setEngineRunning] = useState(false);
  const [startingEngine, setStartingEngine] = useState(false);
  const [showAlerts, setShowAlerts] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [acknowledgedAlerts, setAcknowledgedAlerts] = useState([]);

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
        const p = Number(val || 0);
        if (!p) return;
        next[sym] = [...(next[sym] || []), p].slice(-30);
      });
      return next;
    });
  }, [liveData?.ticks]);

  useEffect(() => {
    const merged = [...(agentData?.agent_events || []), ...(liveData?.agent_events || [])]
      .filter(e => e?.timestamp && e?.message);
    const seen = new Set();
    setEventTape(merged.filter(e => { const k = `${e.timestamp}-${e.message}`; return !seen.has(k) && seen.add(k); }).slice(-25));
  }, [liveData?.agent_events, agentData?.agent_events]);

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
  const latestSignals = (latestDecision?.signals || latestDecision?.signals_raw || []).slice(0, 5);
  const reasoningSignals = latestSignals;
  const progressPct = Number(agentStatus?.progress_pct || 0);
  const pnlColor = (pnl.total || 0) >= 0 ? C.green : C.red;

  const handleStartEngine = async () => {
    setStartingEngine(true);
    try {
      const res = await fetch(`${API_BASE}/api/engine/start`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode: "paper" }) });
      const d = await res.json();
      if (d.status === "starting") { setEngineRunning(true); }
    } catch (e) { alert("❌ Failed to start engine: " + e.message); }
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

  const TABS = [
    { id: "overview", label: "Overview", icon: Activity },
    { id: "positions", label: "Positions & SL", icon: Target },
    { id: "options", label: "Options Flow", icon: Layers },
    { id: "watchlist", label: "Watchlist", icon: Eye },
    { id: "ai", label: "AI Brain", icon: Cpu },
    { id: "orders", label: "Orders", icon: List },
    { id: "system", label: "System", icon: Shield },
    { id: "analytics", label: "Analytics", icon: BarChart2 },
  ];

  return (
    <div style={{ minHeight: "100vh", background: C.bg, color: C.text, fontFamily: "'JetBrains Mono', 'Fira Code', monospace" }}>
      <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700;800&display=swap" rel="stylesheet" />

      <style>{`
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { background: ${C.bg}; }
        ::-webkit-scrollbar { width: 4px; height: 4px; }
        ::-webkit-scrollbar-track { background: ${C.bg}; }
        ::-webkit-scrollbar-thumb { background: ${C.border}; border-radius: 2px; }
        button:hover { opacity: 0.85; }
        @keyframes shimmer { 0%,100%{opacity:1} 50%{opacity:.4} }
        @keyframes pulseDot { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:.5;transform:scale(.8)} }
        @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0} }
      `}</style>

      {/* HEADER */}
      <header style={{ borderBottom: `1px solid ${C.border}`, background: "rgba(6,11,20,0.97)", backdropFilter: "blur(20px)", position: "sticky", top: 0, zIndex: 100 }}>
        <div style={{ maxWidth: 1800, margin: "0 auto", padding: "0 20px", height: 52, display: "flex", alignItems: "center", gap: 20 }}>

          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ background: "linear-gradient(135deg,#00d4a0,#00b4d8)", borderRadius: 7, width: 28, height: 28, display: "flex", alignItems: "center", justifyContent: "center" }}>
              <Zap size={14} color="#060b14" fill="#060b14" />
            </div>
            <div>
              <div style={{ fontWeight: 800, fontSize: 12, letterSpacing: 1 }}>AGENTTRADER</div>
              <div style={{ fontSize: 8, color: C.textMuted, letterSpacing: 2 }}>NSE · F&O · GEMINI AI</div>
            </div>
          </div>

          {/* Index pills */}
          <div style={{ display: "flex", gap: 16, flex: 1 }}>
            {[
              { label: "NIFTY", val: indices.nifty, prev: 22000 },
              { label: "BNIFTY", val: indices.banknifty, prev: 47000 },
              { label: "VIX", val: indices.vix, prev: 14, warn: (indices.vix || 0) > 18 },
            ].map(item => {
              const chg = item.prev ? (((item.val || item.prev) - item.prev) / item.prev * 100) : 0;
              return (
                <div key={item.label} style={{ display: "flex", gap: 8, alignItems: "center", padding: "4px 10px", background: C.surface, borderRadius: 5, border: `1px solid ${C.border}` }}>
                  <span style={{ fontSize: 9, color: C.textMuted, letterSpacing: 1 }}>{item.label}</span>
                  <span style={{ fontSize: 12, fontWeight: 700, color: item.warn ? C.amber : C.text }}>{item.val?.toFixed(2) || "—"}</span>
                  {item.val && <span style={{ fontSize: 9, color: chg >= 0 ? C.green : C.red }}>{chg >= 0 ? "▲" : "▼"}{Math.abs(chg).toFixed(2)}%</span>}
                </div>
              );
            })}
          </div>

          {/* Status bar */}
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
              <Dot active={connected} color={C.green} />
              <span style={{ fontSize: 9, color: connected ? C.green : C.textMuted, letterSpacing: 1 }}>{connected ? "LIVE" : "OFFLINE"}</span>
            </div>
            <span style={{ fontSize: 9, color: C.textDim }}>{lastUpdate.toLocaleTimeString("en-IN")}</span>

            {killSwitch && (
              <button onClick={handleResetKillSwitch} style={{ background: `${C.red}15`, border: `1px solid ${C.red}40`, borderRadius: 5, padding: "4px 10px", cursor: "pointer", fontSize: 9, color: C.red, letterSpacing: 0.5 }}>
                ⚠ KILL SWITCH — RESET
              </button>
            )}

            <button onClick={() => setShowAlerts(p => !p)} style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 5, padding: "5px 9px", cursor: "pointer", color: C.textMuted, position: "relative" }}>
              <Bell size={12} />
            </button>

            <button
              onClick={engineRunning ? handleStopEngine : handleStartEngine}
              disabled={startingEngine}
              style={{ background: engineRunning ? `${C.green}15` : `${C.blue}15`, border: `1px solid ${engineRunning ? C.green : C.blue}40`, borderRadius: 5, padding: "5px 12px", cursor: "pointer", display: "flex", alignItems: "center", gap: 5, fontSize: 9, fontWeight: 800, letterSpacing: 1, color: engineRunning ? C.green : C.blue }}
            >
              <Power size={10} />
              {startingEngine ? "STARTING..." : engineRunning ? "RUNNING" : "START ENGINE"}
            </button>
          </div>
        </div>

        {/* NAV TABS */}
        <div style={{ maxWidth: 1800, margin: "0 auto", padding: "0 20px", display: "flex", gap: 2, borderTop: `1px solid ${C.border}` }}>
          {TABS.map(tab => {
            const Icon = tab.icon;
            return (
              <button key={tab.id} onClick={() => setActiveTab(tab.id)} style={{
                background: "none", border: "none", cursor: "pointer", padding: "9px 14px",
                fontSize: 10, fontWeight: 700, letterSpacing: 0.8,
                color: activeTab === tab.id ? C.cyan : C.textMuted,
                borderBottom: `2px solid ${activeTab === tab.id ? C.cyan : "transparent"}`,
                display: "flex", alignItems: "center", gap: 5, transition: "all .15s",
              }}>
                <Icon size={11} />
                {tab.label.toUpperCase()}
              </button>
            );
          })}
        </div>
      </header>

      <main style={{ maxWidth: 1800, margin: "0 auto", padding: "18px 20px" }}>

        {/* ENGINE STATUS BANNER */}
        {!engineRunning && (
          <div style={{ background: `${C.blue}08`, border: `1px solid ${C.blue}25`, borderRadius: 8, padding: "10px 16px", marginBottom: 16, display: "flex", alignItems: "center", gap: 10 }}>
            <Power size={13} color={C.blue} />
            <span style={{ fontSize: 11, color: C.blue, fontWeight: 700 }}>Engine not running</span>
            <span style={{ fontSize: 11, color: C.textMuted }}>· Live data will appear after starting</span>
            <button onClick={handleStartEngine} style={{ marginLeft: "auto", background: `${C.blue}15`, border: `1px solid ${C.blue}40`, borderRadius: 5, padding: "5px 14px", cursor: "pointer", fontSize: 10, fontWeight: 700, color: C.blue }}>
              START ENGINE →
            </button>
          </div>
        )}

        {/* ── OVERVIEW TAB ── */}
        {activeTab === "overview" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>

            {/* KPI Row */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(6,1fr)", gap: 12 }}>
              <StatCard label="Today P&L" value={<Num v={pnl.total || 0} size={20} />} sub={`${(pnl.pct || 0) >= 0 ? "+" : ""}${(pnl.pct || 0).toFixed(2)}%`} color={pnlColor} icon={pnl.total >= 0 ? TrendingUp : TrendingDown} />
              <StatCard label="Realized" value={<Num v={pnl.realized || 0} size={20} />} sub="Booked P&L" color={C.green} icon={Target} />
              <StatCard label="Available" value={`₹${((funds.available || 0) / 1000).toFixed(1)}K`} sub={`₹${((funds.used_margin || 0) / 1000).toFixed(1)}K margin`} color={C.blue} icon={DollarSign} />
              <StatCard label="Positions" value={positions.length} sub={`${10 - positions.length} slots free`} color={C.purple} icon={Activity} />
              <StatCard label="Win Rate" value={`${(risk.win_rate || 0).toFixed(1)}%`} sub={`${risk.trades_today || 0} trades`} color={C.amber} icon={Shield} />
              <StatCard label="Drawdown" value={`${(risk.drawdown_pct || 0).toFixed(2)}%`} sub={(risk.drawdown_pct || 0) < 2 ? "Safe" : "⚠ Near limit"} color={(risk.drawdown_pct || 0) < 2 ? C.green : C.red} icon={AlertTriangle} />
            </div>

            {/* Charts row */}
            <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 16 }}>
              <Card>
                <SectionHeader title="Intraday P&L" sub="Real-time equity curve" right={<Num v={pnl.total || 0} size={16} />} />
                <div style={{ padding: "14px 18px" }}>
                  <ResponsiveContainer width="100%" height={180}>
                    <AreaChart data={pnlHistory}>
                      <defs>
                        <linearGradient id="pnlG" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor={pnlColor} stopOpacity={0.2} />
                          <stop offset="95%" stopColor={pnlColor} stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke={C.border} />
                      <XAxis dataKey="time" tick={{ fill: C.textMuted, fontSize: 9 }} tickLine={false} axisLine={false} interval={15} />
                      <YAxis tick={{ fill: C.textMuted, fontSize: 9 }} tickLine={false} axisLine={false} tickFormatter={v => `₹${(v / 1000).toFixed(1)}K`} />
                      <Tooltip contentStyle={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 10 }} formatter={v => [`₹${v.toLocaleString("en-IN")}`, "P&L"]} />
                      <ReferenceLine y={0} stroke={C.border} strokeDasharray="4 2" />
                      <Area type="monotone" dataKey="pnl" stroke={pnlColor} fill="url(#pnlG)" strokeWidth={2} dot={false} />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </Card>

              <Card>
                <SectionHeader title="Risk Gauges" sub="Live limit consumption" />
                <div style={{ padding: "14px 18px" }}>
                  {[
                    { label: "Daily Loss Limit", used: Math.abs(Math.min(0, risk.daily_pnl_pct || 0)), max: 2.0, color: C.red },
                    { label: "Max Drawdown", used: risk.drawdown_pct || 0, max: 8.0, color: C.amber },
                    { label: "Positions / 10", used: positions.length, max: 10, color: C.purple },
                    { label: "Margin Used", used: ((funds.used_margin || 0) / (funds.total || 1)) * 100, max: 80, color: C.blue },
                  ].map(r => {
                    const pct = Math.min((r.used / r.max) * 100, 100);
                    return (
                      <div key={r.label} style={{ marginBottom: 14 }}>
                        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 5 }}>
                          <span style={{ fontSize: 10, color: C.textMuted }}>{r.label}</span>
                          <span style={{ fontSize: 10, fontFamily: "monospace", color: pct > 75 ? C.red : C.text }}>
                            {r.label.includes("Pos") ? `${Math.round(r.used)}/${r.max}` : `${r.used.toFixed(1)}%`}
                          </span>
                        </div>
                        <MiniBar value={r.used} max={r.max} color={pct > 75 ? C.red : r.color} />
                      </div>
                    );
                  })}
                  <div style={{ paddingTop: 10, borderTop: `1px solid ${C.border}`, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <span style={{ fontSize: 10, color: C.textMuted }}>Kill Switch</span>
                    <span style={{ fontSize: 10, fontWeight: 700, color: killSwitch ? C.red : C.green }}>{killSwitch ? "⚠ TRIGGERED" : "✓ SAFE"}</span>
                  </div>
                </div>
              </Card>
            </div>

            {/* AI Summary + Live Ticks */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
              <Card>
                <SectionHeader title="AI Cycle Status" sub="Latest decision cycle" right={
                  <div style={{ display: "flex", gap: 6 }}>
                    {latestDecision?.market_regime && <Badge text={latestDecision.market_regime} />}
                  </div>
                } />
                <div style={{ padding: "14px 18px" }}>
                  <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
                    {["collecting_context", "calling_model", "risk_checks", "placing_orders", "decision_complete"].map(s => {
                      const isCurrent = agentStatus?.stage === s;
                      return (
                        <span key={s} style={{ fontSize: 9, padding: "3px 7px", borderRadius: 3, border: `1px solid ${isCurrent ? C.cyan : C.border}`, color: isCurrent ? C.cyan : C.textMuted, background: isCurrent ? `${C.cyan}10` : "transparent" }}>
                          {s.replace(/_/g, " ")}
                        </span>
                      );
                    })}
                  </div>
                  <div style={{ height: 6, background: C.border, borderRadius: 3, overflow: "hidden", marginBottom: 10 }}>
                    <div style={{ width: `${Math.max(4, progressPct)}%`, height: "100%", background: `linear-gradient(90deg,${C.cyan},${C.purple})`, transition: "width .25s" }} />
                  </div>
                  <div style={{ fontSize: 10, color: C.textMuted, marginBottom: 12 }}>{progressPct}% · {agentStatus?.last_cycle_duration_ms || "—"}ms</div>
                  {eventTape.slice(-4).reverse().map((e, i) => (
                    <div key={i} style={{ fontSize: 10, color: e.level === "error" ? C.red : e.level === "success" ? C.green : C.textMuted, marginBottom: 3, display: "flex", gap: 8 }}>
                      <span style={{ color: C.textDim, minWidth: 48 }}>{new Date(e.timestamp).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}</span>
                      {e.message}
                    </div>
                  ))}
                </div>
              </Card>

              <Card>
                <SectionHeader title="Live Ticks" sub="Real-time price feed" right={<Dot active={connected} />} />
                <div style={{ padding: "8px 18px 14px", display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 8 }}>
                  {Object.entries(ticks).slice(0, 9).map(([sym, ltp]) => {
                    const curr = Number(ltp || 0);
                    const prev = Number(prevTicks[sym] || curr);
                    const delta = curr - prev;
                    const series = tickHistory[sym] || [];
                    return (
                      <div key={sym} style={{ background: C.bg, borderRadius: 6, padding: "8px 10px" }}>
                        <div style={{ fontSize: 9, color: C.textMuted, marginBottom: 3 }}>{sym}</div>
                        <div style={{ fontSize: 12, fontWeight: 700, fontFamily: "monospace", color: delta > 0 ? C.green : delta < 0 ? C.red : C.text }}>
                          ₹{curr ? curr.toLocaleString("en-IN") : "—"}
                        </div>
                        <Sparkline data={series.slice(-15)} color={delta >= 0 ? C.green : C.red} height={22} />
                      </div>
                    );
                  })}
                  {!Object.keys(ticks).length && (
                    <div style={{ gridColumn: "1/-1", textAlign: "center", padding: "20px 0", fontSize: 11, color: C.textMuted }}>Waiting for tick data...</div>
                  )}
                </div>
              </Card>
            </div>
          </div>
        )}

        {/* ── POSITIONS & SL TAB ── */}
        {activeTab === "positions" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <PositionSparklinePanel positions={positions} tickHistory={tickHistory} prevTicks={prevTicks} />
            <SLOrderStatusPanel positions={positions} ticks={ticks} />
          </div>
        )}

        {/* ── OPTIONS FLOW TAB ── */}
        {activeTab === "options" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <OptionsChainPanel data={liveData?.options_chain || null} />
            <IntradayChartPanel ticks={ticks} tickHistory={tickHistory} />
          </div>
        )}

        {/* ── WATCHLIST TAB ── */}
        {activeTab === "watchlist" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <WatchlistIndicatorsPanel watchlistData={liveData?.watchlist || null} />
          </div>
        )}

        {/* ── AI BRAIN TAB ── */}
        {activeTab === "ai" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <ConfidenceTimelinePanel decisions={agentDecisions} />
            <StrategyReviewPanel reviewData={null} />
            <ModelFallbackPanel decisions={agentDecisions} />

            {/* Signal cards */}
            <Card>
              <SectionHeader title="Latest AI Signals" sub={`${reasoningSignals.length} signals · ${latestDecision?.timestamp ? new Date(latestDecision.timestamp).toLocaleTimeString("en-IN") : "No live decisions yet"}`} />
              <div style={{ padding: "14px 18px", display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(300px,1fr))", gap: 12 }}>
                {reasoningSignals.map((s, i) => {
                  const conf = Math.round((Number(s.confidence || 0) || 0) * 100);
                  const confColor = conf >= 70 ? C.green : conf >= 50 ? C.amber : C.red;
                  return (
                    <div key={i} style={{ background: C.bg, borderRadius: 8, padding: "14px", border: `1px solid ${C.border}` }}>
                      <div style={{ display: "flex", gap: 8, marginBottom: 10, alignItems: "center" }}>
                        <span style={{ fontWeight: 800, fontSize: 13 }}>{s.symbol}</span>
                        <Badge text={s.action} />
                        <span style={{ marginLeft: "auto" }}><Badge text={s.strategy} /></span>
                      </div>
                      <div style={{ marginBottom: 8 }}>
                        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                          <span style={{ fontSize: 9, color: C.textMuted }}>CONFIDENCE</span>
                          <span style={{ fontSize: 10, fontFamily: "monospace", color: confColor }}>{conf}%</span>
                        </div>
                        <MiniBar value={conf} max={100} color={confColor} />
                      </div>
                      <div style={{ fontFamily: "monospace", fontSize: 10, color: C.textMuted, lineHeight: 1.7 }}>
                        Entry: {s.entry_price ? `₹${Number(s.entry_price).toFixed(0)}` : "—"} · SL: {s.stop_loss ? `₹${Number(s.stop_loss).toFixed(0)}` : "—"} · Tgt: {s.target ? `₹${Number(s.target).toFixed(0)}` : "—"}
                        {s.risk_reward ? ` · R/R: ${Number(s.risk_reward).toFixed(1)}` : ""}
                      </div>
                      <div style={{ fontSize: 10, color: C.text, marginTop: 8, lineHeight: 1.6 }}>{s.rationale}</div>
                      <div style={{ marginTop: 10 }}><Badge text={s.risk_status === "approved" ? "risk_passed" : "risk_rejected"} /></div>
                    </div>
                  );
                })}
                {reasoningSignals.length === 0 && (
                  <div style={{ fontSize: 11, color: C.textMuted }}>No live AI signals yet</div>
                )}
              </div>
            </Card>
          </div>
        )}

        {/* ── ORDERS TAB ── */}
        {activeTab === "orders" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <ExecutionQueuePanel orders={orders} />
            <Card>
              <SectionHeader title="Order History" sub="All orders today" right={
                <button onClick={refetchOrders} style={{ background: "none", border: "none", cursor: "pointer", color: C.textMuted }}><RefreshCw size={12} /></button>
              } />
              <div style={{ padding: "0 18px 14px" }}>
                {orders.length === 0 ? (
                  <div style={{ textAlign: "center", padding: "40px 0", color: C.textMuted, fontSize: 12 }}>No orders today</div>
                ) : (
                  <table style={{ width: "100%", borderCollapse: "collapse" }}>
                    <thead>
                      <tr>{["Time", "Symbol", "Side", "Qty", "Price", "Avg Fill", "Status", "Tag", "Slippage"].map(h =>
                        <th key={h} style={{ padding: "8px 6px", textAlign: "left", fontSize: 9, color: C.textMuted, letterSpacing: 1.2, borderBottom: `1px solid ${C.border}` }}>{h}</th>
                      )}</tr>
                    </thead>
                    <tbody>
                      {orders.slice(0, 30).map((o, i) => {
                        const slip = o.price && o.average_price ? ((o.average_price - o.price) / o.price * 100) : null;
                        return (
                          <tr key={i} style={{ borderBottom: `1px solid ${C.bg}` }}>
                            <td style={{ padding: "9px 6px", fontSize: 10, color: C.textMuted }}>{new Date(o.placed_at).toLocaleTimeString("en-IN")}</td>
                            <td style={{ padding: "9px 6px", fontWeight: 800, fontSize: 11 }}>{o.symbol}</td>
                            <td style={{ padding: "9px 6px" }}><Badge text={o.side} /></td>
                            <td style={{ padding: "9px 6px", fontFamily: "monospace", fontSize: 11 }}>{o.quantity}</td>
                            <td style={{ padding: "9px 6px", fontFamily: "monospace", fontSize: 11 }}>{o.price ? `₹${o.price}` : "MKT"}</td>
                            <td style={{ padding: "9px 6px", fontFamily: "monospace", fontSize: 11, color: C.textMuted }}>{o.average_price ? `₹${o.average_price}` : "—"}</td>
                            <td style={{ padding: "9px 6px" }}><Badge text={o.status} /></td>
                            <td style={{ padding: "9px 6px", fontSize: 10, color: C.textMuted }}>{o.tag || "—"}</td>
                            <td style={{ padding: "9px 6px", fontSize: 10, fontFamily: "monospace", color: slip !== null && Math.abs(slip) > 0.1 ? C.amber : C.textMuted }}>
                              {slip !== null ? `${slip.toFixed(3)}%` : "—"}
                            </td>
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

        {/* ── SYSTEM TAB ── */}
        {activeTab === "system" && (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
            <KillSwitchHistoryPanel risk={risk} riskEvents={riskEvents} />
            <ModelFallbackPanel decisions={agentDecisions} />
            <Card style={{ gridColumn: "1/-1" }}>
              <SectionHeader title="Event Log" sub="Full AI agent pipeline events" />
              <div style={{ padding: "10px 18px 14px", maxHeight: 320, overflowY: "auto" }}>
                {eventTape.length === 0 ? (
                  <div style={{ fontSize: 11, color: C.textMuted, padding: "20px 0" }}>No events yet</div>
                ) : eventTape.slice().reverse().map((e, i) => (
                  <div key={i} style={{ display: "flex", gap: 10, padding: "6px 8px", borderRadius: 4, marginBottom: 2, background: i % 2 === 0 ? C.bg : "transparent" }}>
                    <span style={{ fontSize: 9, color: C.textDim, minWidth: 52, fontFamily: "monospace" }}>{new Date(e.timestamp).toLocaleTimeString("en-IN")}</span>
                    <span style={{ width: 4, height: 4, borderRadius: "50%", background: e.level === "error" ? C.red : e.level === "success" ? C.green : C.amber, marginTop: 5, flexShrink: 0 }} />
                    <span style={{ fontSize: 10, color: e.level === "error" ? C.red : e.level === "success" ? C.green : C.textMuted, flex: 1 }}>{e.message}</span>
                    <span style={{ fontSize: 9, color: C.textDim }}>{e.stage || ""}</span>
                  </div>
                ))}
              </div>
            </Card>
          </div>
        )}

        {/* ── ANALYTICS TAB ── */}
        {activeTab === "analytics" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(6,1fr)", gap: 12 }}>
              {[
                { label: "30D P&L", value: `₹${((analyticsData?.total_pnl || 0) / 1000).toFixed(1)}K` },
                { label: "Total Trades", value: analyticsData?.total_trades || 0 },
                { label: "Win Rate", value: `${(analyticsData?.win_rate || 0).toFixed(1)}%` },
                { label: "Avg Win", value: `₹${(analyticsData?.avg_win || 0).toFixed(0)}` },
                { label: "Avg Loss", value: `₹${(analyticsData?.avg_loss || 0).toFixed(0)}` },
                { label: "Profit Factor", value: (analyticsData?.profit_factor || 0).toFixed(2) },
              ].map(s => <StatCard key={s.label} label={s.label} value={s.value} color={C.cyan} />)}
            </div>

            <Card>
              <SectionHeader title="14-Day P&L History" />
              <div style={{ padding: "14px 18px" }}>
                {dailyHistory?.history?.length ? (
                  <ResponsiveContainer width="100%" height={200}>
                    <BarChart data={dailyHistory.history.slice().reverse()}>
                      <CartesianGrid strokeDasharray="3 3" stroke={C.border} />
                      <XAxis dataKey="date" tick={{ fill: C.textMuted, fontSize: 9 }} tickLine={false} axisLine={false} />
                      <YAxis tick={{ fill: C.textMuted, fontSize: 9 }} tickLine={false} axisLine={false} tickFormatter={v => `₹${(v / 1000).toFixed(1)}K`} />
                      <Tooltip contentStyle={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 10 }} formatter={v => [`₹${v.toLocaleString("en-IN")}`, "Net P&L"]} />
                      <ReferenceLine y={0} stroke={C.border} />
                      <Bar dataKey="net_pnl" radius={[3, 3, 0, 0]}>
                        {(dailyHistory.history || []).map((entry, i) => (
                          <Cell key={i} fill={(entry.net_pnl || 0) >= 0 ? C.green : C.red} opacity={0.75} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                ) : (
                  <div style={{ textAlign: "center", padding: "40px", color: C.textMuted, fontSize: 12 }}>No history yet</div>
                )}
              </div>
            </Card>

            <ConfidenceTimelinePanel decisions={agentDecisions} />
          </div>
        )}
      </main>
    </div>
  );
}

import asyncio
import json
from datetime import datetime
from decimal import Decimal

from agents.brain import MarketContext, TradingAgent
from core.pipeline_models import TradeCandidate


def _context(vix: float = 14.0, trend: str = "trending_up", session: str = "mid_session") -> MarketContext:
    return MarketContext(
        timestamp=datetime(2026, 3, 23, 10, 0, 0),
        nifty50_ltp=22500.0,
        banknifty_ltp=48500.0,
        india_vix=vix,
        market_trend=trend,
        session=session,
        day_of_week="Monday",
        available_capital=100000.0,
        used_margin=0.0,
        open_positions=[],
        watchlist_data=[],
        options_chain_summary=None,
        recent_news_sentiment="Neutral",
        pcr=1.0,
    )


def _candidate(candidate_id: str, symbol: str, signal_strength: float = 0.85, priority: int = 10) -> TradeCandidate:
    return TradeCandidate(
        candidate_id=candidate_id,
        symbol=symbol,
        exchange="NSE",
        side="BUY",
        setup_type="breakout",
        strategy="momentum",
        timeframe="5m",
        product="MIS",
        entry_price=Decimal("100"),
        stop_loss=Decimal("95"),
        target=Decimal("110"),
        risk_reward=2.0,
        signal_strength=signal_strength,
        trend_score=0.7,
        liquidity_score=0.8,
        volatility_regime="normal",
        sector_tag="technology",
        ltp_reference=Decimal("100"),
        max_affordable_qty=10,
        generated_at=datetime(2026, 3, 23, 10, 0, 0),
        priority=priority,
    )


def test_mode_mapping():
    agent = TradingAgent({"confidence_threshold": 0.65})

    assert agent._infer_operating_mode(_context(vix=24.0), [_candidate("c1", "AAA")]) == "avoid_trading"
    assert agent._infer_operating_mode(_context(vix=19.0), [_candidate("c1", "AAA")]) == "capital_preservation"
    assert agent._infer_operating_mode(_context(vix=14.0, trend="trending_up"), [_candidate("c1", "AAA"), _candidate("c2", "BBB")]) == "active_trading"


def test_confidence_floor_behavior(monkeypatch):
    agent = TradingAgent({"confidence_threshold": 0.70})
    candidates = [_candidate("cand-1", "AAA")]

    async def fake_generate_text(prompt, **kwargs):
        return json.dumps({
            "market_regime": "trend",
            "operating_mode": "active_trading",
            "market_commentary": "Trend ok.",
            "candidate_evaluations": [
                {
                    "candidate_id": "cand-1",
                    "approved": True,
                    "confidence": 0.60,
                    "rationale": "Too low but model tried to approve.",
                    "priority": 1,
                    "risk_notes": [],
                }
            ],
        }), "mock-model"

    monkeypatch.setattr(agent, "_generate_text", fake_generate_text)
    result = asyncio.run(agent.evaluate_candidates(candidates, _context()))

    assert result.candidate_evaluations[0].approved is False
    assert any("confidence floor" in note.lower() for note in result.candidate_evaluations[0].risk_notes)


def test_max_entries_behavior(monkeypatch):
    agent = TradingAgent({"confidence_threshold": 0.65})
    candidates = [_candidate("cand-1", "AAA"), _candidate("cand-2", "BBB"), _candidate("cand-3", "CCC")]

    async def fake_generate_text(prompt, **kwargs):
        return json.dumps({
            "market_regime": "trend",
            "operating_mode": "selective",
            "market_commentary": "Take only the best setup.",
            "candidate_evaluations": [
                {"candidate_id": "cand-1", "approved": True, "confidence": 0.90, "rationale": "A", "priority": 3, "risk_notes": []},
                {"candidate_id": "cand-2", "approved": True, "confidence": 0.88, "rationale": "B", "priority": 2, "risk_notes": []},
                {"candidate_id": "cand-3", "approved": True, "confidence": 0.87, "rationale": "C", "priority": 1, "risk_notes": []},
            ],
        }), "mock-model"

    monkeypatch.setattr(agent, "_generate_text", fake_generate_text)
    result = asyncio.run(agent.evaluate_candidates(candidates, _context()))

    assert sum(1 for item in result.candidate_evaluations if item.approved) == 1
    assert result.mode_constraints["max_new_entries"] == 1


def test_hard_ceiling_enforcement(monkeypatch):
    agent = TradingAgent({
        "confidence_threshold": 0.65,
        "ai_absolute_max_new_entries": 1,
        "ai_absolute_capital_multiplier": 0.60,
    })
    candidates = [_candidate("cand-1", "AAA"), _candidate("cand-2", "BBB")]

    async def fake_generate_text(prompt, **kwargs):
        return json.dumps({
            "market_regime": "trend",
            "operating_mode": "active_trading",
            "market_commentary": "Take multiple entries.",
            "candidate_evaluations": [
                {"candidate_id": "cand-1", "approved": True, "confidence": 0.91, "rationale": "A", "priority": 2, "risk_notes": []},
                {"candidate_id": "cand-2", "approved": True, "confidence": 0.89, "rationale": "B", "priority": 1, "risk_notes": []},
            ],
        }), "mock-model"

    monkeypatch.setattr(agent, "_generate_text", fake_generate_text)
    result = asyncio.run(agent.evaluate_candidates(candidates, _context()))

    assert sum(1 for item in result.candidate_evaluations if item.approved) == 1
    assert result.mode_constraints["max_new_entries"] == 1
    assert result.mode_constraints["capital_multiplier"] == 0.6


def test_invalid_confidence_payload_is_safely_rejected(monkeypatch):
    agent = TradingAgent({"confidence_threshold": 0.65})
    candidates = [_candidate("cand-1", "AAA")]

    async def fake_generate_text(prompt, **kwargs):
        return json.dumps({
            "market_regime": "trend",
            "operating_mode": "active_trading",
            "market_commentary": "Malformed confidence format.",
            "candidate_evaluations": [
                {
                    "candidate_id": "cand-1",
                    "approved": True,
                    "confidence": "Below 0.65",
                    "rationale": "Bad confidence format",
                    "priority": 1,
                    "risk_notes": [],
                }
            ],
        }), "mock-model"

    monkeypatch.setattr(agent, "_generate_text", fake_generate_text)
    result = asyncio.run(agent.evaluate_candidates(candidates, _context()))

    assert result.candidate_evaluations[0].approved is False
    assert result.candidate_evaluations[0].confidence == 0.0
    assert any("invalid confidence format from model" in note.lower() for note in result.candidate_evaluations[0].risk_notes)

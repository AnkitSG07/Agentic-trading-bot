import asyncio
import json
from datetime import datetime
from decimal import Decimal

from agents.brain import MarketContext, TradingAgent
from core.pipeline_models import AIEvaluationResult, TradeCandidate


def _context() -> MarketContext:
    return MarketContext(
        timestamp=datetime(2026, 3, 23, 10, 0, 0),
        nifty50_ltp=22500.0,
        banknifty_ltp=48500.0,
        india_vix=14.0,
        market_trend="trending_up",
        session="mid_session",
        day_of_week="Monday",
        available_capital=100000.0,
        used_margin=0.0,
        open_positions=[],
        watchlist_data=[],
        options_chain_summary=None,
        recent_news_sentiment="Positive",
        pcr=1.1,
    )


def _candidate(candidate_id: str, symbol: str, priority: int = 10) -> TradeCandidate:
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
        signal_strength=0.82,
        trend_score=0.7,
        liquidity_score=0.8,
        volatility_regime="normal",
        sector_tag="technology",
        ltp_reference=Decimal("100"),
        max_affordable_qty=10,
        generated_at=datetime(2026, 3, 23, 10, 0, 0),
        priority=priority,
    )


def test_evaluate_candidates_returns_ai_evaluation_result(monkeypatch):
    agent = TradingAgent({"confidence_threshold": 0.65})
    candidates = [_candidate("cand-1", "AAA")]

    async def fake_generate_text(prompt, **kwargs):
        return json.dumps({
            "market_regime": "trend",
            "operating_mode": "active_trading",
            "market_commentary": "Constructive trend conditions.",
            "candidate_evaluations": [
                {
                    "candidate_id": "cand-1",
                    "approved": True,
                    "confidence": 0.84,
                    "rationale": "Aligned momentum and liquidity.",
                    "priority": 3,
                    "risk_notes": [],
                }
            ],
        }), "mock-model"

    monkeypatch.setattr(agent, "_generate_text", fake_generate_text)
    result = asyncio.run(agent.evaluate_candidates(candidates, _context()))

    assert isinstance(result, AIEvaluationResult)
    assert result.market_regime == "trend"
    assert result.operating_mode == "active_trading"
    assert result.market_commentary


def test_candidate_evaluation_schema_and_session_outputs_are_present(monkeypatch):
    agent = TradingAgent({"confidence_threshold": 0.65})
    candidates = [_candidate("cand-1", "AAA")]

    async def fake_generate_text(prompt, **kwargs):
        return json.dumps({
            "market_regime": "trend",
            "operating_mode": "selective",
            "market_commentary": "Only best setups should pass.",
            "candidate_evaluations": [
                {
                    "candidate_id": "cand-1",
                    "approved": True,
                    "confidence": 0.9,
                    "rationale": "High-quality breakout.",
                    "priority": 1,
                    "risk_notes": ["Monitor volatility expansion"],
                }
            ],
        }), "mock-model"

    monkeypatch.setattr(agent, "_generate_text", fake_generate_text)
    result = asyncio.run(agent.evaluate_candidates(candidates, _context()))
    evaluation = result.candidate_evaluations[0]

    assert evaluation.candidate_id == "cand-1"
    assert isinstance(evaluation.approved, bool)
    assert isinstance(evaluation.confidence, float)
    assert isinstance(evaluation.rationale, str)
    assert isinstance(evaluation.priority, int)
    assert isinstance(evaluation.risk_notes, list)
    assert result.mode_constraints["confidence_floor"] >= 0.65
    assert "max_new_entries" in result.mode_constraints


def test_ai_cannot_invent_extra_candidates(monkeypatch):
    agent = TradingAgent({"confidence_threshold": 0.65})
    candidates = [_candidate("cand-1", "AAA"), _candidate("cand-2", "BBB")]

    async def fake_generate_text(prompt, **kwargs):
        return json.dumps({
            "market_regime": "trend",
            "operating_mode": "active_trading",
            "market_commentary": "Trend remains favorable.",
            "candidate_evaluations": [
                {
                    "candidate_id": "cand-1",
                    "approved": True,
                    "confidence": 0.82,
                    "rationale": "AAA remains valid.",
                    "priority": 1,
                    "risk_notes": [],
                },
                {
                    "candidate_id": "invented-999",
                    "approved": True,
                    "confidence": 0.99,
                    "rationale": "Should be ignored.",
                    "priority": 0,
                    "risk_notes": [],
                },
            ],
        }), "mock-model"

    monkeypatch.setattr(agent, "_generate_text", fake_generate_text)
    result = asyncio.run(agent.evaluate_candidates(candidates, _context()))

    assert [item.candidate_id for item in result.candidate_evaluations] == ["cand-1", "cand-2"]
    assert all(item.candidate_id != "invented-999" for item in result.candidate_evaluations)

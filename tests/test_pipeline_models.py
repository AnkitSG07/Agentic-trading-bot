from datetime import datetime
from decimal import Decimal

from core.pipeline_models import AICandidateEvaluation, ApprovedCandidate, TradeCandidate


def test_approved_candidate_exposes_common_properties():
    candidate = TradeCandidate(
        candidate_id="cand-1",
        symbol="RELIANCE",
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
        signal_strength=0.9,
        trend_score=0.8,
        liquidity_score=0.7,
        volatility_regime="normal",
        sector_tag="energy",
        ltp_reference=Decimal("100"),
        max_affordable_qty=10,
        generated_at=datetime(2026, 3, 23, 9, 0),
        priority=5,
    )
    evaluation = AICandidateEvaluation(
        candidate_id="cand-1",
        approved=True,
        confidence=0.88,
        rationale="Aligned trend and momentum",
        priority=1,
    )

    approved = ApprovedCandidate(candidate=candidate, evaluation=evaluation)

    assert approved.candidate_id == "cand-1"
    assert approved.symbol == "RELIANCE"
    assert approved.side == "BUY"
    assert approved.confidence == 0.88

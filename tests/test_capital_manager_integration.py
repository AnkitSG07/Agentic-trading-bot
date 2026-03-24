from datetime import datetime
from decimal import Decimal

from brokers.base import Funds
from capital_manager import CapitalManager
from core.pipeline_models import AICandidateEvaluation, ApprovedCandidate, TradeCandidate


def _approved_candidate(symbol: str, *, priority: int, confidence: float, entry_price: str, max_affordable_qty: int, risk_reward: float = 2.0, side: str = "BUY") -> ApprovedCandidate:
    candidate = TradeCandidate(
        candidate_id=f"{symbol}:{side}:2026-03-23T10:00:00",
        symbol=symbol,
        exchange="NSE",
        side=side,
        setup_type="breakout",
        strategy="momentum",
        timeframe="5m",
        product="MIS",
        entry_price=Decimal(entry_price),
        stop_loss=Decimal("95") if side == "BUY" else Decimal("105"),
        target=Decimal("110") if side == "BUY" else Decimal("90"),
        risk_reward=risk_reward,
        signal_strength=0.9,
        trend_score=0.8,
        liquidity_score=0.7,
        volatility_regime="normal",
        sector_tag="energy",
        ltp_reference=Decimal(entry_price),
        max_affordable_qty=max_affordable_qty,
        generated_at=datetime(2026, 3, 23, 10, 0),
        priority=priority,
    )
    evaluation = AICandidateEvaluation(
        candidate_id=candidate.candidate_id,
        approved=True,
        confidence=confidence,
        rationale="Looks strong",
        priority=priority,
    )
    return ApprovedCandidate(candidate=candidate, evaluation=evaluation)


def test_plan_from_candidates_ranks_sizes_and_skips_open_symbols():
    manager = CapitalManager({"min_cash_reserve": 100, "max_capital_per_trade_pct": 0.5, "transaction_cost_pct": 0.0015})
    funds = Funds(available_cash=Decimal("10000"), used_margin=Decimal("0"), total_balance=Decimal("10000"))

    plans = manager.plan_from_candidates(
        [
            _approved_candidate("AAA", priority=3, confidence=0.72, entry_price="100", max_affordable_qty=50),
            _approved_candidate("BBB", priority=1, confidence=0.95, entry_price="200", max_affordable_qty=20),
            _approved_candidate("CCC", priority=5, confidence=0.91, entry_price="150", max_affordable_qty=30),
        ],
        funds,
        open_position_symbols={"CCC"},
    )

    assert [plan.symbol for plan in plans] == ["AAA", "BBB"]
    assert plans[0].quantity == 47
    assert plans[0].capital_allocated == Decimal("4700.00")
    assert plans[0].source_candidate_id == "AAA:BUY:2026-03-23T10:00:00"
    assert plans[1].quantity == 11
    assert plans[1].capital_allocated == Decimal("2200.00")


def test_plan_from_candidates_applies_rr_floor_and_hard_order_cap():
    manager = CapitalManager({
        "min_cash_reserve": 50,
        "max_capital_per_trade_pct": 0.9,
        "max_order_value_absolute": 1000,
    })
    funds = Funds(available_cash=Decimal("5000"), used_margin=Decimal("0"), total_balance=Decimal("5000"))

    plans = manager.plan_from_candidates(
        [
            _approved_candidate("LOWRR", priority=5, confidence=0.9, entry_price="100", max_affordable_qty=20, risk_reward=1.2),
            _approved_candidate("OK", priority=4, confidence=0.8, entry_price="250", max_affordable_qty=10),
        ],
        funds,
    )

    assert [plan.symbol for plan in plans] == ["OK"]
    assert plans[0].quantity == 3
    assert plans[0].capital_allocated == Decimal("750.00")

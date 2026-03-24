from datetime import datetime
from decimal import Decimal

from core.pipeline_models import AICandidateEvaluation, ApprovedCandidate, OrderPlan, TradeCandidate
from risk.portfolio_guard import PortfolioGuard, PortfolioGuardConfig


def _approved(symbol: str, *, sector: str | None, priority: int = 5, confidence: float = 0.8, event_flags: list[str] | None = None) -> ApprovedCandidate:
    candidate = TradeCandidate(
        candidate_id=f"{symbol}:BUY:2026-03-23T10:00:00",
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
        signal_strength=0.8,
        trend_score=0.7,
        liquidity_score=0.7,
        volatility_regime="normal",
        sector_tag=sector,
        ltp_reference=Decimal("100"),
        max_affordable_qty=10,
        generated_at=datetime(2026, 3, 23, 10, 0),
        priority=priority,
        event_flags=list(event_flags or []),
    )
    evaluation = AICandidateEvaluation(
        candidate_id=candidate.candidate_id,
        approved=True,
        confidence=confidence,
        rationale="ok",
        priority=priority,
    )
    return ApprovedCandidate(candidate=candidate, evaluation=evaluation)


def _plan(approved: ApprovedCandidate) -> OrderPlan:
    candidate = approved.candidate
    return OrderPlan(
        symbol=candidate.symbol,
        exchange=candidate.exchange,
        side=candidate.side,
        quantity=5,
        entry_price=candidate.entry_price,
        stop_loss=candidate.stop_loss,
        target=candidate.target,
        product=candidate.product,
        order_type="LIMIT",
        strategy_tag=candidate.strategy,
        capital_allocated=Decimal("500"),
        risk_reward=candidate.risk_reward,
        confidence=approved.evaluation.confidence,
        source_candidate_id=candidate.candidate_id,
    )


def test_portfolio_guard_blocks_duplicates_event_flags_and_sector_overload():
    guard = PortfolioGuard(PortfolioGuardConfig(max_open_positions=4, max_per_sector=2))
    candidates = [
        _approved("AAA", sector="energy", priority=5),
        _approved("BBB", sector="energy", priority=4),
        _approved("CCC", sector="energy", priority=3),
        _approved("DDD", sector="banks", priority=6, event_flags=["earnings:today"]),
        _approved("EEE", sector="tech", priority=2),
    ]

    result = guard.filter_candidates(
        candidates,
        open_position_symbols={"AAA"},
        open_sector_counts={"energy": 1},
        open_positions_count=1,
    )

    assert [item.candidate.symbol for item in result.approved] == ["BBB", "EEE"]
    assert result.blocked["AAA:BUY:2026-03-23T10:00:00"] == "symbol already open"
    assert result.blocked["CCC:BUY:2026-03-23T10:00:00"] == "sector cap reached for energy"
    assert result.blocked["DDD:BUY:2026-03-23T10:00:00"] == "blocked by event flag"


def test_portfolio_guard_honors_global_position_cap():
    guard = PortfolioGuard(PortfolioGuardConfig(max_open_positions=2, max_per_sector=2))
    candidates = [_approved("AAA", sector="energy", priority=5), _approved("BBB", sector="banks", priority=4)]

    result = guard.filter_candidates(candidates, open_positions_count=2)

    assert result.approved == []
    assert result.blocked["AAA:BUY:2026-03-23T10:00:00"] == "portfolio position cap reached"
    assert result.blocked["BBB:BUY:2026-03-23T10:00:00"] == "portfolio position cap reached"


def test_portfolio_guard_honors_directional_bias_caps():
    guard = PortfolioGuard(PortfolioGuardConfig(max_open_positions=5, max_per_sector=5, max_long_positions=1, max_short_positions=1))
    buy_candidate = _approved("AAA", sector="energy", priority=5)
    short_candidate = _approved("BBB", sector="banks", priority=4)
    short_candidate.candidate.side = "SHORT"

    result = guard.filter_candidates(
        [buy_candidate, short_candidate],
        open_positions=[
            {"symbol": "OPENLONG", "side": "BUY", "strategy": "momentum", "sector_tag": "energy"},
            {"symbol": "OPENSHORT", "side": "SHORT", "strategy": "mean_reversion", "sector_tag": "banks"},
        ],
        open_positions_count=2,
    )

    assert result.approved == []
    assert result.blocked[buy_candidate.candidate_id] == "long bias cap reached"
    assert result.blocked[short_candidate.candidate_id] == "short bias cap reached"


def test_portfolio_guard_honors_correlation_and_strategy_caps():
    guard = PortfolioGuard(PortfolioGuardConfig(
        max_open_positions=5,
        max_per_sector=5,
        correlation_cap=1,
        max_strategy_allocation=0.5,
    ))
    correlated = _approved("AAA", sector="energy", priority=5)
    strategy_heavy = _approved("BBB", sector="tech", priority=4)

    result = guard.filter_candidates(
        [correlated, strategy_heavy],
        open_positions=[
            {"symbol": "OPEN1", "side": "BUY", "strategy": "momentum", "sector_tag": "energy"},
            {"symbol": "OPEN2", "side": "BUY", "strategy": "momentum", "sector_tag": "banks"},
        ],
        open_positions_count=2,
    )

    assert result.approved == []
    assert result.blocked[correlated.candidate_id] == "correlation cap reached for energy"
    assert result.blocked[strategy_heavy.candidate_id] == "strategy allocation cap reached for momentum"


def test_portfolio_guard_can_filter_order_plans_with_candidate_lookup():
    guard = PortfolioGuard(PortfolioGuardConfig(max_open_positions=4, max_per_sector=1))
    approved = _approved("AAA", sector="energy", priority=5)
    result = guard.check(
        [_plan(approved)],
        candidate_lookup={approved.candidate_id: approved},
        open_positions=[
            {"symbol": "OPEN1", "side": "BUY", "strategy": "swing", "sector_tag": "energy"},
        ],
        open_positions_count=1,
    )

    assert result.approved == []
    assert result.blocked[approved.candidate_id] == "sector cap reached for energy"

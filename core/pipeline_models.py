from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional


@dataclass(slots=True)
class TradeCandidate:
    candidate_id: str
    symbol: str
    exchange: str
    side: str
    setup_type: str
    strategy: str
    timeframe: str
    product: str
    entry_price: Decimal
    stop_loss: Decimal
    target: Decimal
    risk_reward: float
    signal_strength: float
    trend_score: float
    liquidity_score: float
    volatility_regime: str
    sector_tag: Optional[str]
    ltp_reference: Decimal
    max_affordable_qty: int
    generated_at: datetime
    priority: int = 0
    caution_flags: list[str] = field(default_factory=list)
    event_flags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AICandidateEvaluation:
    candidate_id: str
    approved: bool
    confidence: float
    rationale: str
    priority: int
    risk_notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AIEvaluationResult:
    candidate_evaluations: list[AICandidateEvaluation]
    market_regime: Optional[str] = None
    operating_mode: Optional[str] = None
    market_commentary: Optional[str] = None
    mode_constraints: dict[str, float | int] = field(default_factory=dict)


@dataclass(slots=True)
class ApprovedCandidate:
    candidate: TradeCandidate
    evaluation: AICandidateEvaluation

    @property
    def candidate_id(self) -> str:
        return self.candidate.candidate_id

    @property
    def symbol(self) -> str:
        return self.candidate.symbol

    @property
    def side(self) -> str:
        return self.candidate.side

    @property
    def confidence(self) -> float:
        return self.evaluation.confidence


@dataclass(slots=True)
class OrderPlan:
    symbol: str
    exchange: str
    side: str
    quantity: int
    entry_price: Decimal
    stop_loss: Decimal
    target: Decimal
    product: str
    order_type: str
    strategy_tag: str
    capital_allocated: Decimal
    risk_reward: float
    confidence: float
    source_candidate_id: str


@dataclass(slots=True)
class ExecutionFill:
    order_id: str
    broker_order_id: Optional[str]
    fill_price: Decimal
    fill_qty: int
    fill_time: datetime
    slippage: Decimal
    status: str


@dataclass(slots=True)
class PreflightCheck:
    check_name: str
    passed: bool
    severity: str
    message: str
    recommended_action: Optional[str] = None


@dataclass(slots=True)
class PreflightResult:
    checks: list[PreflightCheck]
    all_passed: bool
    blocking_reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ReconciliationStatus:
    positions_match: bool
    orders_match: bool
    drift_details: list[str] = field(default_factory=list)
    action_taken: Optional[str] = None


@dataclass(slots=True)
class HealthStatus:
    broker_ok: bool
    data_feed_ok: bool
    ai_ok: bool
    last_checked: datetime
    degraded_reason: Optional[str] = None
    severity: str = "info"
    recommended_action: Optional[str] = None

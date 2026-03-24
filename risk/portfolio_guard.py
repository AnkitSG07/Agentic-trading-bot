from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from core.pipeline_models import ApprovedCandidate, OrderPlan


@dataclass(slots=True)
class PortfolioGuardConfig:
    max_open_positions: int = 10
    max_per_sector: int = 2
    correlation_cap: int = 2
    max_long_positions: int = 10
    max_short_positions: int = 10
    max_strategy_allocation: float = 0.5
    blocked_event_flags: tuple[str, ...] = ("earnings:today", "results:today", "event:block", "news:block")


@dataclass(slots=True)
class PortfolioGuardResult:
    approved: list[ApprovedCandidate] = field(default_factory=list)
    blocked: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class PortfolioPlanGuardResult:
    approved: list[OrderPlan] = field(default_factory=list)
    blocked: dict[str, str] = field(default_factory=dict)


class PortfolioGuard:
    def __init__(self, config: PortfolioGuardConfig | None = None) -> None:
        self.config = config or PortfolioGuardConfig()

    def filter_candidates(
        self,
        approved_candidates: Iterable[ApprovedCandidate],
        *,
        open_position_symbols: set[str] | None = None,
        open_sector_counts: dict[str, int] | None = None,
        open_positions_count: int = 0,
        open_positions: Iterable[object] | None = None,
    ) -> PortfolioGuardResult:
        open_position_symbols = {symbol.upper() for symbol in (open_position_symbols or set())}
        active_positions = int(open_positions_count)
        current_positions = list(open_positions or [])
        sector_counts = self._sector_counts(current_positions, open_sector_counts)
        direction_counts = self._direction_counts(current_positions)
        strategy_counts = self._strategy_counts(current_positions)
        correlation_counts = self._correlation_counts(current_positions, sector_counts)
        result = PortfolioGuardResult()

        for approved in sorted(
            approved_candidates,
            key=lambda item: (-item.evaluation.priority, -item.evaluation.confidence, item.candidate.symbol),
        ):
            candidate = approved.candidate
            symbol = candidate.symbol.upper()
            sector = candidate.sector_tag or "__unclassified__"
            side = str(candidate.side or "").upper()
            strategy = str(candidate.strategy or "__unknown__")
            direction = "long" if side in {"BUY", "COVER"} else "short"
            correlation_group = self._correlation_group(candidate)

            if symbol in open_position_symbols:
                result.blocked[candidate.candidate_id] = "symbol already open"
                continue
            if any(flag in self.config.blocked_event_flags for flag in candidate.event_flags):
                result.blocked[candidate.candidate_id] = "blocked by event flag"
                continue
            if active_positions >= self.config.max_open_positions:
                result.blocked[candidate.candidate_id] = "portfolio position cap reached"
                continue
            if sector_counts.get(sector, 0) >= self.config.max_per_sector:
                result.blocked[candidate.candidate_id] = f"sector cap reached for {sector}"
                continue
            if direction == "long" and direction_counts["long"] >= self.config.max_long_positions:
                result.blocked[candidate.candidate_id] = "long bias cap reached"
                continue
            if direction == "short" and direction_counts["short"] >= self.config.max_short_positions:
                result.blocked[candidate.candidate_id] = "short bias cap reached"
                continue
            if correlation_counts.get(correlation_group, 0) >= self.config.correlation_cap:
                result.blocked[candidate.candidate_id] = f"correlation cap reached for {correlation_group}"
                continue
            projected_strategy_count = strategy_counts.get(strategy, 0) + 1
            max_strategy_positions = max(1, int(self.config.max_open_positions * self.config.max_strategy_allocation))
            if projected_strategy_count > max_strategy_positions:
                result.blocked[candidate.candidate_id] = f"strategy allocation cap reached for {strategy}"
                continue

            result.approved.append(approved)
            active_positions += 1
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
            direction_counts[direction] += 1
            strategy_counts[strategy] = projected_strategy_count
            correlation_counts[correlation_group] = correlation_counts.get(correlation_group, 0) + 1

        return result

    def check(
        self,
        plans: Iterable[OrderPlan],
        *,
        candidate_lookup: dict[str, ApprovedCandidate | object] | None = None,
        open_position_symbols: set[str] | None = None,
        open_sector_counts: dict[str, int] | None = None,
        open_positions_count: int = 0,
        open_positions: Iterable[object] | None = None,
    ) -> PortfolioPlanGuardResult:
        open_position_symbols = {symbol.upper() for symbol in (open_position_symbols or set())}
        active_positions = int(open_positions_count)
        current_positions = list(open_positions or [])
        sector_counts = self._sector_counts(current_positions, open_sector_counts)
        direction_counts = self._direction_counts(current_positions)
        strategy_counts = self._strategy_counts(current_positions)
        correlation_counts = self._correlation_counts(current_positions, sector_counts)
        result = PortfolioPlanGuardResult()
        candidate_lookup = candidate_lookup or {}

        for plan in sorted(
            plans,
            key=lambda item: (-float(item.confidence), -float(item.risk_reward), item.symbol),
        ):
            metadata = self._resolve_plan_metadata(plan, candidate_lookup)
            symbol = plan.symbol.upper()
            sector = metadata["sector"]
            direction = metadata["direction"]
            strategy = metadata["strategy"]
            correlation_group = metadata["correlation_group"]
            block_key = str(plan.source_candidate_id or symbol)

            if symbol in open_position_symbols:
                result.blocked[block_key] = "symbol already open"
                continue
            if active_positions >= self.config.max_open_positions:
                result.blocked[block_key] = "portfolio position cap reached"
                continue
            if sector_counts.get(sector, 0) >= self.config.max_per_sector:
                result.blocked[block_key] = f"sector cap reached for {sector}"
                continue
            if direction == "long" and direction_counts["long"] >= self.config.max_long_positions:
                result.blocked[block_key] = "long bias cap reached"
                continue
            if direction == "short" and direction_counts["short"] >= self.config.max_short_positions:
                result.blocked[block_key] = "short bias cap reached"
                continue
            if correlation_counts.get(correlation_group, 0) >= self.config.correlation_cap:
                result.blocked[block_key] = f"correlation cap reached for {correlation_group}"
                continue
            projected_strategy_count = strategy_counts.get(strategy, 0) + 1
            max_strategy_positions = max(1, int(self.config.max_open_positions * self.config.max_strategy_allocation))
            if projected_strategy_count > max_strategy_positions:
                result.blocked[block_key] = f"strategy allocation cap reached for {strategy}"
                continue

            result.approved.append(plan)
            active_positions += 1
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
            direction_counts[direction] += 1
            strategy_counts[strategy] = projected_strategy_count
            correlation_counts[correlation_group] = correlation_counts.get(correlation_group, 0) + 1

        return result

    @staticmethod
    def _extract_attr(position: object, name: str, default=None):
        if isinstance(position, dict):
            return position.get(name, default)
        return getattr(position, name, default)

    def _direction_counts(self, open_positions: Iterable[object]) -> dict[str, int]:
        counts = {"long": 0, "short": 0}
        for position in open_positions:
            side = str(self._extract_attr(position, "side", "") or "").upper()
            qty = self._extract_attr(position, "qty", 0)
            if not side:
                side = "BUY" if float(qty or 0) >= 0 else "SHORT"
            direction = "long" if side in {"BUY", "COVER"} else "short"
            counts[direction] += 1
        return counts

    def _strategy_counts(self, open_positions: Iterable[object]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for position in open_positions:
            strategy = str(self._extract_attr(position, "strategy", "__unknown__") or "__unknown__")
            counts[strategy] = counts.get(strategy, 0) + 1
        return counts

    def _sector_counts(
        self,
        open_positions: Iterable[object],
        open_sector_counts: dict[str, int] | None,
    ) -> dict[str, int]:
        counts = {str(k): int(v) for k, v in (open_sector_counts or {}).items()}
        for position in open_positions:
            sector = str(
                self._extract_attr(position, "sector_tag")
                or self._extract_attr(position, "sector")
                or f"symbol:{str(self._extract_attr(position, 'symbol', '__unknown__')).upper()}"
            )
            counts[sector] = counts.get(sector, 0) + 1
        return counts

    def _correlation_counts(
        self,
        open_positions: Iterable[object],
        sector_counts: dict[str, int],
    ) -> dict[str, int]:
        counts = dict(sector_counts)
        for position in open_positions:
            group = str(
                self._extract_attr(position, "sector_tag")
                or self._extract_attr(position, "sector")
                or self._extract_attr(position, "correlation_group")
                or f"symbol:{str(self._extract_attr(position, 'symbol', '__unknown__')).upper()}"
            )
            counts[group] = counts.get(group, 0) + 1
        return counts

    @staticmethod
    def _correlation_group(candidate) -> str:
        return str(candidate.sector_tag or f"symbol:{candidate.symbol.upper()}")

    def _resolve_plan_metadata(
        self,
        plan: OrderPlan,
        candidate_lookup: dict[str, ApprovedCandidate | object],
    ) -> dict[str, str]:
        candidate_ref = candidate_lookup.get(plan.source_candidate_id)
        candidate = getattr(candidate_ref, "candidate", candidate_ref)
        sector = str(
            getattr(candidate, "sector_tag", None)
            or getattr(candidate, "sector", None)
            or f"symbol:{plan.symbol.upper()}"
        )
        side = str(getattr(candidate, "side", None) or plan.side or "").upper()
        direction = "long" if side in {"BUY", "COVER"} else "short"
        strategy = str(getattr(candidate, "strategy", None) or plan.strategy_tag or "__unknown__")
        correlation_group = str(
            getattr(candidate, "sector_tag", None)
            or getattr(candidate, "correlation_group", None)
            or sector
        )
        return {
            "sector": sector,
            "direction": direction,
            "strategy": strategy,
            "correlation_group": correlation_group,
        }

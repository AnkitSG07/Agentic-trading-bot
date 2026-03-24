from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Optional

from core.pipeline_models import HealthStatus


@dataclass(slots=True)
class PreflightConfig:
    market_data_max_age_seconds: int = 120
    runtime_broker_failure_action: str = "block new entries"
    runtime_data_failure_action: str = "exits only"
    runtime_ai_failure_action: str = "block new entries"
    runtime_risk_failure_action: str = "full trading pause"


@dataclass(slots=True)
class PreflightReport:
    statuses: list[HealthStatus]
    overall_ok: bool
    recommended_action: str
    blocking_reasons: list[str] = field(default_factory=list)


class EnginePreflight:
    def __init__(self, config: PreflightConfig | None = None) -> None:
        self.config = config or PreflightConfig()

    async def run_startup(
        self,
        *,
        broker_connected: bool,
        funds_probe: Callable[[], Awaitable[object]],
        positions_probe: Callable[[], Awaitable[object]],
        orders_probe: Callable[[], Awaitable[object]],
        market_data_fresh: bool,
        ai_reachable: bool,
        repository_available: bool,
        risk_allows_trading: bool,
        tradable_session: bool,
        now: Optional[datetime] = None,
    ) -> PreflightReport:
        checked_at = now or datetime.utcnow()
        statuses: list[HealthStatus] = []
        blocking_reasons: list[str] = []

        async def probe(name: str, fn: Callable[[], Awaitable[object]]) -> bool:
            try:
                await fn()
                return True
            except Exception:
                blocking_reasons.append(f"{name} failed")
                return False

        funds_ok = await probe("funds", funds_probe)
        positions_ok = await probe("positions", positions_probe)
        orders_ok = await probe("orders", orders_probe)

        broker_ok = broker_connected and funds_ok and positions_ok and orders_ok
        if not broker_connected:
            blocking_reasons.append("broker connectivity unavailable")
        if not market_data_fresh:
            blocking_reasons.append("market data is stale")
        if not ai_reachable:
            blocking_reasons.append("AI provider unavailable")
        if not repository_available:
            blocking_reasons.append("repository unavailable")
        if not risk_allows_trading:
            blocking_reasons.append("risk state blocks trading")
        if not tradable_session:
            blocking_reasons.append("session not tradable")

        statuses.append(self._status(checked_at, broker_ok=broker_ok, data_feed_ok=market_data_fresh, ai_ok=ai_reachable, degraded_reason=None if broker_ok else "startup broker checks failed", severity="critical" if not broker_ok else "info", recommended_action="full trading pause" if not broker_ok else "continue"))
        statuses.append(self._status(checked_at, broker_ok=broker_ok, data_feed_ok=market_data_fresh, ai_ok=ai_reachable, degraded_reason=None if market_data_fresh else "market data freshness check failed", severity="critical" if not market_data_fresh else "info", recommended_action="full trading pause" if not market_data_fresh else "continue"))
        statuses.append(self._status(checked_at, broker_ok=broker_ok, data_feed_ok=market_data_fresh, ai_ok=ai_reachable, degraded_reason=None if ai_reachable else "AI provider unavailable", severity="critical" if not ai_reachable else "info", recommended_action="full trading pause" if not ai_reachable else "continue"))
        statuses.append(self._status(checked_at, broker_ok=broker_ok, data_feed_ok=market_data_fresh, ai_ok=ai_reachable, degraded_reason=None if repository_available else "repository unavailable", severity="critical" if not repository_available else "info", recommended_action="full trading pause" if not repository_available else "continue"))
        statuses.append(self._status(checked_at, broker_ok=broker_ok, data_feed_ok=market_data_fresh, ai_ok=ai_reachable, degraded_reason=None if risk_allows_trading else "kill switch or risk block active", severity="critical" if not risk_allows_trading else "info", recommended_action="full trading pause" if not risk_allows_trading else "continue"))
        statuses.append(self._status(checked_at, broker_ok=broker_ok, data_feed_ok=market_data_fresh, ai_ok=ai_reachable, degraded_reason=None if tradable_session else "session not tradable", severity="critical" if not tradable_session else "info", recommended_action="full trading pause" if not tradable_session else "continue"))

        overall_ok = not blocking_reasons
        return PreflightReport(statuses=statuses, overall_ok=overall_ok, recommended_action="continue" if overall_ok else "full trading pause", blocking_reasons=blocking_reasons)

    async def run_runtime(
        self,
        *,
        broker_ok: bool,
        market_data_fresh: bool,
        ai_ok: bool,
        risk_ok: bool,
        now: Optional[datetime] = None,
    ) -> PreflightReport:
        checked_at = now or datetime.utcnow()
        statuses: list[HealthStatus] = []
        blocking_reasons: list[str] = []
        recommended_action = "continue"

        if not broker_ok:
            blocking_reasons.append("broker degraded")
            recommended_action = self._highest_action(recommended_action, self.config.runtime_broker_failure_action)
        if not market_data_fresh:
            blocking_reasons.append("market data stale")
            recommended_action = self._highest_action(recommended_action, self.config.runtime_data_failure_action)
        if not ai_ok:
            blocking_reasons.append("AI unavailable")
            recommended_action = self._highest_action(recommended_action, self.config.runtime_ai_failure_action)
        if not risk_ok:
            blocking_reasons.append("risk state degraded")
            recommended_action = self._highest_action(recommended_action, self.config.runtime_risk_failure_action)

        statuses.append(self._status(checked_at, broker_ok=broker_ok, data_feed_ok=market_data_fresh, ai_ok=ai_ok, degraded_reason=None if broker_ok else "broker health degraded", severity=self._severity_for_action(self.config.runtime_broker_failure_action) if not broker_ok else "info", recommended_action=self.config.runtime_broker_failure_action if not broker_ok else "continue"))
        statuses.append(self._status(checked_at, broker_ok=broker_ok, data_feed_ok=market_data_fresh, ai_ok=ai_ok, degraded_reason=None if market_data_fresh else "market data stale", severity=self._severity_for_action(self.config.runtime_data_failure_action) if not market_data_fresh else "info", recommended_action=self.config.runtime_data_failure_action if not market_data_fresh else "continue"))
        statuses.append(self._status(checked_at, broker_ok=broker_ok, data_feed_ok=market_data_fresh, ai_ok=ai_ok, degraded_reason=None if ai_ok else "AI provider unavailable", severity=self._severity_for_action(self.config.runtime_ai_failure_action) if not ai_ok else "info", recommended_action=self.config.runtime_ai_failure_action if not ai_ok else "continue"))
        statuses.append(self._status(checked_at, broker_ok=broker_ok, data_feed_ok=market_data_fresh, ai_ok=ai_ok, degraded_reason=None if risk_ok else "risk state blocks new entries", severity=self._severity_for_action(self.config.runtime_risk_failure_action) if not risk_ok else "info", recommended_action=self.config.runtime_risk_failure_action if not risk_ok else "continue"))

        return PreflightReport(statuses=statuses, overall_ok=not blocking_reasons, recommended_action=recommended_action, blocking_reasons=blocking_reasons)

    @staticmethod
    def _status(checked_at: datetime, *, broker_ok: bool, data_feed_ok: bool, ai_ok: bool, degraded_reason: Optional[str], severity: str, recommended_action: str) -> HealthStatus:
        return HealthStatus(
            broker_ok=broker_ok,
            data_feed_ok=data_feed_ok,
            ai_ok=ai_ok,
            last_checked=checked_at,
            degraded_reason=degraded_reason,
            severity=severity,
            recommended_action=recommended_action,
        )

    @staticmethod
    def _severity_for_action(action: str) -> str:
        normalized = action.strip().lower()
        if normalized == "continue":
            return "info"
        if normalized == "block new entries":
            return "warning"
        if normalized == "exits only":
            return "high"
        return "critical"

    @staticmethod
    def _highest_action(current: str, candidate: str) -> str:
        order = {
            "continue": 0,
            "block new entries": 1,
            "exits only": 2,
            "full trading pause": 3,
        }
        return candidate if order[candidate] > order[current] else current

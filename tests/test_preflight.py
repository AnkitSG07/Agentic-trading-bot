from datetime import datetime

import pytest

from core.preflight import EnginePreflight, PreflightConfig


@pytest.mark.anyio
async def test_startup_preflight_success():
    preflight = EnginePreflight()

    async def ok_probe():
        return {"ok": True}

    report = await preflight.run_startup(
        broker_connected=True,
        funds_probe=ok_probe,
        positions_probe=ok_probe,
        orders_probe=ok_probe,
        market_data_fresh=True,
        ai_reachable=True,
        repository_available=True,
        risk_allows_trading=True,
        tradable_session=True,
        now=datetime(2026, 3, 23, 9, 20),
    )

    assert report.overall_ok is True
    assert report.recommended_action == "continue"
    assert report.blocking_reasons == []


@pytest.mark.anyio
async def test_startup_preflight_blocks_on_critical_failure():
    preflight = EnginePreflight()

    async def failing_probe():
        raise RuntimeError("no funds")

    async def ok_probe():
        return []

    report = await preflight.run_startup(
        broker_connected=False,
        funds_probe=failing_probe,
        positions_probe=ok_probe,
        orders_probe=ok_probe,
        market_data_fresh=False,
        ai_reachable=True,
        repository_available=False,
        risk_allows_trading=False,
        tradable_session=True,
        now=datetime(2026, 3, 23, 9, 20),
    )

    assert report.overall_ok is False
    assert report.recommended_action == "full trading pause"
    assert "funds failed" in report.blocking_reasons
    assert "broker connectivity unavailable" in report.blocking_reasons
    assert "repository unavailable" in report.blocking_reasons


@pytest.mark.anyio
async def test_runtime_preflight_degraded_health_detection():
    preflight = EnginePreflight(PreflightConfig())
    report = await preflight.run_runtime(
        broker_ok=True,
        market_data_fresh=False,
        ai_ok=False,
        risk_ok=True,
        now=datetime(2026, 3, 23, 10, 0),
    )

    assert report.overall_ok is False
    assert report.recommended_action == "exits only"
    assert "market data stale" in report.blocking_reasons
    assert "AI unavailable" in report.blocking_reasons


@pytest.mark.anyio
async def test_runtime_action_severity_mapping():
    preflight = EnginePreflight(PreflightConfig(
        runtime_broker_failure_action="block new entries",
        runtime_data_failure_action="exits only",
        runtime_ai_failure_action="block new entries",
        runtime_risk_failure_action="full trading pause",
    ))
    report = await preflight.run_runtime(
        broker_ok=False,
        market_data_fresh=False,
        ai_ok=True,
        risk_ok=False,
        now=datetime(2026, 3, 23, 10, 0),
    )

    assert report.recommended_action == "full trading pause"
    severities = {status.recommended_action: status.severity for status in report.statuses if status.degraded_reason}
    assert severities["block new entries"] == "warning"
    assert severities["exits only"] == "high"
    assert severities["full trading pause"] == "critical"

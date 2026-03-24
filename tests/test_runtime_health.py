from datetime import datetime

import pytest

from core.preflight import EnginePreflight, PreflightConfig


@pytest.mark.anyio
async def test_runtime_health_broker_degraded():
    preflight = EnginePreflight(PreflightConfig(runtime_broker_failure_action="block new entries"))
    report = await preflight.run_runtime(
        broker_ok=False,
        market_data_fresh=True,
        ai_ok=True,
        risk_ok=True,
        now=datetime(2026, 3, 23, 11, 0),
    )

    assert report.recommended_action == "block new entries"
    assert "broker degraded" in report.blocking_reasons


@pytest.mark.anyio
async def test_runtime_health_stale_data_feed():
    preflight = EnginePreflight(PreflightConfig(runtime_data_failure_action="exits only"))
    report = await preflight.run_runtime(
        broker_ok=True,
        market_data_fresh=False,
        ai_ok=True,
        risk_ok=True,
        now=datetime(2026, 3, 23, 11, 1),
    )

    assert report.recommended_action == "exits only"
    assert "market data stale" in report.blocking_reasons


@pytest.mark.anyio
async def test_runtime_health_ai_unavailable():
    preflight = EnginePreflight(PreflightConfig(runtime_ai_failure_action="block new entries"))
    report = await preflight.run_runtime(
        broker_ok=True,
        market_data_fresh=True,
        ai_ok=False,
        risk_ok=True,
        now=datetime(2026, 3, 23, 11, 2),
    )

    assert report.recommended_action == "block new entries"
    assert "AI unavailable" in report.blocking_reasons


@pytest.mark.anyio
async def test_runtime_health_combines_to_stricter_action():
    preflight = EnginePreflight(PreflightConfig(
        runtime_broker_failure_action="block new entries",
        runtime_data_failure_action="exits only",
        runtime_ai_failure_action="block new entries",
        runtime_risk_failure_action="full trading pause",
    ))
    report = await preflight.run_runtime(
        broker_ok=False,
        market_data_fresh=False,
        ai_ok=False,
        risk_ok=True,
        now=datetime(2026, 3, 23, 11, 3),
    )

    assert report.recommended_action == "exits only"

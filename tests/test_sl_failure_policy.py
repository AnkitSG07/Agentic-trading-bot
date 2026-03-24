from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
import sys
import types

import pytest

from brokers.base import Exchange, Instrument, InstrumentType, Order, OrderSide, OrderStatus, OrderType, ProductType
from core.pipeline_models import OrderPlan

repository_stub = types.ModuleType("database.repository")
class _Repo: pass
repository_stub.AgentDecisionRepository = _Repo
repository_stub.OHLCVRepository = _Repo
repository_stub.PositionRepository = _Repo
repository_stub.RiskEventRepository = _Repo
repository_stub.SLOrderRepository = _Repo
repository_stub.TradeRepository = _Repo
sys.modules.setdefault("database.repository", repository_stub)

from core import engine as engine_module
from core.engine import TradingEngine


class TrackerStub:
    def add(self, **kwargs):
        self.last = kwargs


class SLPolicyBroker:
    def __init__(self, fail_sl_times=0):
        self.fail_sl_times = fail_sl_times
        self.sl_attempts = 0
        self.flattened = False

    async def place_order(self, **kwargs):
        order_type = kwargs["order_type"]
        if order_type == OrderType.SL_M:
            self.sl_attempts += 1
            if self.sl_attempts <= self.fail_sl_times:
                raise RuntimeError("SL rejected")
            return Order("sl-1", "sl-1", kwargs["instrument"], kwargs["side"], order_type, kwargs["product"], kwargs["quantity"], None, kwargs.get("trigger_price"), OrderStatus.PENDING)
        if order_type == OrderType.MARKET and kwargs.get("tag") == "SLFAIL_FLAT":
            self.flattened = True
        return Order("entry-1", "entry-1", kwargs["instrument"], kwargs["side"], order_type, kwargs["product"], kwargs["quantity"], kwargs.get("price"), kwargs.get("trigger_price"), OrderStatus.PENDING)

    async def get_order_status(self, order_id):
        return Order(
            order_id="entry-1",
            broker_order_id="entry-1",
            instrument=Instrument("AAA", Exchange.NSE, InstrumentType.EQ),
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            product=ProductType.MIS,
            quantity=5,
            price=Decimal("100"),
            trigger_price=None,
            status=OrderStatus.COMPLETE,
            filled_quantity=5,
            average_price=Decimal("100.5"),
        )


def _engine(policy: str, broker: SLPolicyBroker):
    engine = TradingEngine.__new__(TradingEngine)
    engine.execution_primary_broker = "dhan"
    engine._primary_broker_name = "dhan"
    engine._replication_enabled = False
    engine.replica_broker = None
    engine._replication_status = "disabled"
    engine._last_replication_error = ""
    engine.tracker = TrackerStub()
    engine.sl_protection_failure_policy = policy
    engine.sl_protection_retry_count = 1
    state = {"kill_switch_reason": None}
    engine.risk = SimpleNamespace(_trigger_kill_switch=lambda reason: state.update(kill_switch_reason=reason))
    engine._risk_state = state
    async def _get_instrument(symbol, exchange):
        return Instrument(symbol=symbol, exchange=Exchange(exchange), instrument_type=InstrumentType.EQ, instrument_token="1")
    async def _notify_entry(signal, qty, sl):
        return None
    engine._get_instrument = _get_instrument
    engine._notify_entry = _notify_entry
    engine.get_execution_broker = lambda: broker
    return engine




async def _noop_trade(**kwargs):
    return SimpleNamespace()


async def _open_position(**kwargs):
    return SimpleNamespace(id="pos-1")


async def _noop_async(*args, **kwargs):
    return None

def _plan():
    return OrderPlan(
        symbol="AAA",
        exchange="NSE",
        side="BUY",
        quantity=5,
        entry_price=Decimal("100"),
        stop_loss=Decimal("95"),
        target=Decimal("110"),
        product="MIS",
        order_type="LIMIT",
        strategy_tag="momentum",
        capital_allocated=Decimal("500"),
        risk_reward=2.0,
        confidence=0.8,
        source_candidate_id="cand-1",
    )


@pytest.mark.anyio
async def test_retry_then_flatten(monkeypatch):
    broker = SLPolicyBroker(fail_sl_times=2)
    engine = _engine("flatten", broker)
    monkeypatch.setattr(engine_module, "TradeRepository", SimpleNamespace(save=_noop_trade))
    monkeypatch.setattr(engine_module, "PositionRepository", SimpleNamespace(open_position=_open_position))
    monkeypatch.setattr(engine_module, "SLOrderRepository", SimpleNamespace(save=_noop_async))
    monkeypatch.setattr(engine_module, "RiskEventRepository", SimpleNamespace(log=_noop_async))

    ok = await engine._execute_from_plan(_plan())

    assert ok is False
    assert broker.sl_attempts == 2
    assert broker.flattened is True


@pytest.mark.anyio
async def test_retry_then_pause(monkeypatch):
    broker = SLPolicyBroker(fail_sl_times=2)
    engine = _engine("pause", broker)
    monkeypatch.setattr(engine_module, "TradeRepository", SimpleNamespace(save=_noop_trade))
    monkeypatch.setattr(engine_module, "PositionRepository", SimpleNamespace(open_position=_open_position))
    monkeypatch.setattr(engine_module, "SLOrderRepository", SimpleNamespace(save=_noop_async))
    monkeypatch.setattr(engine_module, "RiskEventRepository", SimpleNamespace(log=_noop_async))

    ok = await engine._execute_from_plan(_plan())

    assert ok is False
    assert broker.flattened is False
    assert "Protective stop could not be confirmed" in engine._risk_state["kill_switch_reason"]


@pytest.mark.anyio
async def test_safe_handling_when_sl_placement_succeeds(monkeypatch):
    broker = SLPolicyBroker(fail_sl_times=0)
    engine = _engine("flatten", broker)
    monkeypatch.setattr(engine_module, "TradeRepository", SimpleNamespace(save=_noop_trade))
    monkeypatch.setattr(engine_module, "PositionRepository", SimpleNamespace(open_position=_open_position))
    monkeypatch.setattr(engine_module, "SLOrderRepository", SimpleNamespace(save=_noop_async))
    monkeypatch.setattr(engine_module, "RiskEventRepository", SimpleNamespace(log=_noop_async))

    ok = await engine._execute_from_plan(_plan())

    assert ok is True
    assert broker.sl_attempts == 1
    assert broker.flattened is False

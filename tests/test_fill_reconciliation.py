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


class BrokerStub:
    def __init__(self, status_order: Order):
        self.status_order = status_order
        self.orders = []

    async def place_order(self, **kwargs):
        self.orders.append(kwargs)
        return Order(
            order_id="entry-1" if len(self.orders) == 1 else f"sl-{len(self.orders)}",
            broker_order_id="entry-1" if len(self.orders) == 1 else f"sl-{len(self.orders)}",
            instrument=kwargs["instrument"],
            side=kwargs["side"],
            order_type=kwargs["order_type"],
            product=kwargs["product"],
            quantity=kwargs["quantity"],
            price=kwargs.get("price"),
            trigger_price=kwargs.get("trigger_price"),
            status=OrderStatus.PENDING,
        )

    async def get_order_status(self, order_id):
        return self.status_order


class TrackerStub:
    def add(self, **kwargs):
        self.last = kwargs


def _engine_with_broker(broker):
    engine = TradingEngine.__new__(TradingEngine)
    engine.execution_primary_broker = "dhan"
    engine._primary_broker_name = "dhan"
    engine._replication_enabled = False
    engine.replica_broker = None
    engine._replication_status = "disabled"
    engine._last_replication_error = ""
    engine.tracker = TrackerStub()
    engine.sl_protection_failure_policy = "flatten"
    engine.sl_protection_retry_count = 1
    engine.risk = SimpleNamespace(_trigger_kill_switch=lambda reason: None)
    async def _get_instrument(symbol, exchange):
        return Instrument(symbol=symbol, exchange=Exchange(exchange), instrument_type=InstrumentType.EQ, instrument_token="1")
    async def _notify_entry(signal, qty, sl):
        return None
    engine._get_instrument = _get_instrument
    engine._notify_entry = _notify_entry
    engine.get_execution_broker = lambda: broker
    return engine




async def _save_trade(saved, **kwargs):
    saved.update(kwargs)
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
async def test_actual_fill_captured_and_persisted(monkeypatch):
    saved = {}
    broker = BrokerStub(Order(
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
        average_price=Decimal("101.5"),
    ))
    engine = _engine_with_broker(broker)

    monkeypatch.setattr(engine_module, "TradeRepository", SimpleNamespace(save=lambda **kwargs: _save_trade(saved, **kwargs)))
    monkeypatch.setattr(engine_module, "PositionRepository", SimpleNamespace(open_position=_open_position))
    monkeypatch.setattr(engine_module, "SLOrderRepository", SimpleNamespace(save=_noop_async))
    monkeypatch.setattr(engine_module, "RiskEventRepository", SimpleNamespace(log=_noop_async))

    ok = await engine._execute_from_plan(_plan())

    assert ok is True
    assert saved["price"] == Decimal("101.5")
    assert saved["average_price"] == Decimal("101.5")
    assert saved["status"] == "COMPLETE"


@pytest.mark.anyio
async def test_pending_reconciliation_when_fill_unavailable(monkeypatch):
    saved = {}
    broker = BrokerStub(Order(
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
        average_price=None,
    ))
    engine = _engine_with_broker(broker)

    monkeypatch.setattr(engine_module, "TradeRepository", SimpleNamespace(save=lambda **kwargs: _save_trade(saved, **kwargs)))
    monkeypatch.setattr(engine_module, "PositionRepository", SimpleNamespace(open_position=_open_position))
    monkeypatch.setattr(engine_module, "SLOrderRepository", SimpleNamespace(save=_noop_async))
    monkeypatch.setattr(engine_module, "RiskEventRepository", SimpleNamespace(log=_noop_async))

    ok = await engine._execute_from_plan(_plan())

    assert ok is True
    assert saved["status"] == "PENDING_RECONCILIATION"
    assert saved["price"] == Decimal("100")


@pytest.mark.anyio
async def test_zero_fill_placeholder_is_not_used(monkeypatch):
    saved = {}
    broker = BrokerStub(Order(
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
        average_price=Decimal("0"),
    ))
    engine = _engine_with_broker(broker)

    monkeypatch.setattr(engine_module, "TradeRepository", SimpleNamespace(save=lambda **kwargs: _save_trade(saved, **kwargs)))
    monkeypatch.setattr(engine_module, "PositionRepository", SimpleNamespace(open_position=_open_position))
    monkeypatch.setattr(engine_module, "SLOrderRepository", SimpleNamespace(save=_noop_async))
    monkeypatch.setattr(engine_module, "RiskEventRepository", SimpleNamespace(log=_noop_async))

    ok = await engine._execute_from_plan(_plan())

    assert ok is True
    assert saved["price"] != Decimal("0")
    assert saved["status"] == "PENDING_RECONCILIATION"


@pytest.mark.anyio
async def test_pending_reconciliation_updates_trade_and_position(monkeypatch):
    saved = {}
    updates = {}
    broker = BrokerStub(Order(
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
        average_price=None,
    ))
    engine = _engine_with_broker(broker)
    engine._pending_execution_reconciliation = {}

    async def _update_status(**kwargs):
        updates["trade"] = kwargs

    async def _update_entry_price(position_id, entry_price):
        updates["position"] = {"position_id": position_id, "entry_price": entry_price}

    monkeypatch.setattr(engine_module, "TradeRepository", SimpleNamespace(
        save=lambda **kwargs: _save_trade(saved, **kwargs),
        update_status=_update_status,
    ))
    monkeypatch.setattr(engine_module, "PositionRepository", SimpleNamespace(
        open_position=_open_position,
        update_entry_price=_update_entry_price,
    ))
    monkeypatch.setattr(engine_module, "SLOrderRepository", SimpleNamespace(
        save=_noop_async,
        get_active_for_position=lambda _position_id: _noop_async(),
    ))
    monkeypatch.setattr(engine_module, "RiskEventRepository", SimpleNamespace(log=_noop_async))

    ok = await engine._execute_from_plan(_plan())
    assert ok is True
    assert "entry-1" in engine._pending_execution_reconciliation

    broker.status_order = Order(
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
        average_price=Decimal("101.2"),
    )

    await engine._reconcile_pending_execution_state()

    assert updates["trade"]["broker_order_id"] == "entry-1"
    assert updates["trade"]["avg_price"] == Decimal("101.2")
    assert updates["position"]["position_id"] == "pos-1"
    assert updates["position"]["entry_price"] == Decimal("101.2")
    assert engine._pending_execution_reconciliation == {}

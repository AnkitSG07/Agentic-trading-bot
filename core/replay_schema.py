"""Lightweight replay request schemas used by API and tests."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ReplayRunCreateRequest(BaseModel):
    symbols: list[str]
    exchange: str = "NSE"
    timeframe: str = "day"
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    initial_capital: float = Field(default=100000, gt=0)
    fee_pct: float = Field(default=0.0003, ge=0, le=0.1)
    slippage_pct: float = Field(default=0.0005, ge=0, le=0.1)
    latency_slippage_bps: float = Field(default=2.0, ge=0, le=1000)
    partial_fill_probability: float = Field(default=0.15, ge=0, le=1)
    ai_every_n_candles: int = Field(default=1, ge=1, le=240)
    confidence_threshold: Optional[float] = Field(default=None, ge=0.30, le=0.95)

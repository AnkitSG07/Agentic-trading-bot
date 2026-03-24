"""Lightweight replay request schemas used by API and tests."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class ReplayRunCreateRequest(BaseModel):
    symbols: list[str] = Field(default_factory=list)
    exchange: str = "NSE"
    timeframe: str = "day"
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    initial_capital: float = Field(default=100000, gt=0)
    fee_pct: float = Field(default=0.0003, ge=0, le=0.1)
    slippage_pct: float = Field(default=0.0005, ge=0, le=0.1)
    latency_slippage_bps: float = Field(default=2.0, ge=0, le=1000)
    ai_every_n_candles: int = Field(default=1, ge=1, le=240)
    confidence_threshold: Optional[float] = Field(default=None, ge=0.30, le=0.95)
    selection_mode: Literal["manual", "auto"] = "manual"
    budget_cap: Optional[float] = Field(default=None, gt=0)
    max_auto_symbols: int = Field(default=5, ge=1, le=25)

    @model_validator(mode="after")
    def validate_symbol_selection(self) -> "ReplayRunCreateRequest":
        self.symbols = [str(symbol).strip().upper() for symbol in self.symbols if str(symbol).strip()]
        if self.selection_mode == "manual":
            if not self.symbols:
                raise ValueError("manual mode requires at least one symbol")
            return self
        if self.budget_cap is None:
            raise ValueError("auto mode requires budget_cap")
        return self

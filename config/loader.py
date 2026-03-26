"""
Shared config loader.
Reads config/config.yaml and expands all ${VAR} and ${VAR:-default} references
from the process environment.  Import this everywhere instead of calling
yaml.safe_load directly.
"""

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Load .env once when the module is first imported
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)


DEFAULT_CONFIG: dict = {
    "session": {
        "blocked_entry_windows": [
            {"start": "09:15", "end": "09:30", "reason": "Opening range entry block"},
            {"start": "15:15", "end": "15:30", "reason": "Closing auction entry block"},
        ],
        "allow_exits_during_entry_blocks": True,
    },
    "risk": {
        "min_risk_reward": 1.5,
        "min_expected_edge_score": 0.55,
        "sector_concentration_cap": 2,
        "correlation_cap": 2,
        "long_bias_cap": 10,
        "short_bias_cap": 10,
        "strategy_family_cap": 0.5,
    },
    "engine": {
        "market_data_max_age_seconds": 120,
        "health_check_interval_seconds": 60,
        "reconciliation_interval_seconds": 120,
        "pause_on_mismatch": False,
        "sl_protection_failure_policy": "flatten",
    },
    "agent": {
        "ai_absolute_max_new_entries": 1,
        "ai_absolute_capital_multiplier": 1.0,
        "min_expected_edge_score": 0.55,
        "fallback_min_trend_liquidity": 0.50,
        "fallback_replay_allow_top1": True,
        "fallback_replay_confidence_floor": 0.60,
        "max_new_entries_per_cycle": 2,
    },
    "replay": {
        "slippage_pct": 0.0005,
        "latency_slippage_bps": 2.0,
        "stop_target_precedence": "stop_first",
        "order_type": "MARKET",
        "circuit_breaker_cooldown": 2,
        "decision_timeout_seconds": 4.0,
        "provider_timeout_seconds": 1.8,
        "max_models_per_decision": 2,
    },
    "news": {
        "enabled": True,
        "freshness_limit_minutes": 240,
        "confidence_modifier_cap": 0.2,
    },
}


def _expand(value):
    """Recursively expand ${VAR} / ${VAR:-default} in any YAML value."""
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}"):
            inner = value[2:-1]
            if ":-" in inner:
                key, default = inner.split(":-", 1)
                return os.getenv(key.strip(), default.strip())
            return os.getenv(inner.strip(), value)  # return original if not set
        return value
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def _deep_merge(defaults, overrides):
    if isinstance(defaults, dict) and isinstance(overrides, dict):
        merged = {k: _deep_merge(v, overrides.get(k)) for k, v in defaults.items()}
        for key, value in overrides.items():
            if key not in merged:
                merged[key] = value
        return merged
    if overrides is None:
        return defaults
    return overrides


@lru_cache(maxsize=1)
def load_config() -> dict:
    """
    Load and return the fully-expanded config dict.
    Result is cached so repeated calls are free.
    Call config.load_config.cache_clear() in tests to reset.
    """
    # __file__ is config/loader.py, so .parent is the config/ dir,
    # and .parent.parent is the project root. The yaml file sits at config/config.yaml.
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        raw = yaml.safe_load(f)
    return _expand(_deep_merge(DEFAULT_CONFIG, raw or {}))

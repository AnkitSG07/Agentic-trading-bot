"""Tests for the AI model chain configuration after the overhaul."""

import ast
from pathlib import Path


def _active_config_text() -> str:
    cfg = Path("config/config.yaml").read_text()
    return "\n".join(line for line in cfg.splitlines() if not line.strip().startswith("#"))


# ─── Config file assertions ──────────────────────────────────────────────────

def test_xiaomimimo_removed_from_config():
    cfg = _active_config_text()
    assert "xiaomimimo" not in cfg, "xiaomimimo should be removed from config.yaml"
    assert "XIAOMI_MIMO_API_KEY" not in cfg


def test_xiaomimimo_removed_from_brain():
    src = Path("agents/brain.py").read_text()
    # No active code references (comments about removal are fine)
    assert "xiaomi_mimo_api_key" not in src, "xiaomi_mimo_api_key attribute should be removed"
    assert '"xiaomimimo/mimo-v2-flash"' not in src
    assert "api.xiaomimimo.com" not in src


def test_groq_70b_models_in_config():
    cfg = Path("config/config.yaml").read_text()
    assert "groq/llama-3.1-70b-versatile" in cfg
    assert "groq/llama-3.3-70b-specdec" in cfg


def test_free_openrouter_models_in_config():
    cfg = Path("config/config.yaml").read_text()
    assert "openrouter/stepfun/step-3.5-flash:free" in cfg
    assert "openrouter/qwen/qwen3-next-80b-a3b-instruct:free" in cfg
    assert "openrouter/openai/gpt-oss-120b:free" in cfg
    assert "openrouter/meta-llama/llama-3.3-70b-instruct:free" in cfg
    assert "openrouter/arcee-ai/trinity-large-preview:free" in cfg


def test_deepseek_v3_removed_from_config():
    cfg = Path("config/config.yaml").read_text()
    assert "openrouter/deepseek/deepseek-v3" not in cfg


def test_groq_70b_models_in_brain_default_tiers():
    src = Path("agents/brain.py").read_text()
    assert '"groq/llama-3.1-70b-versatile"' in src
    assert '"groq/llama-3.3-70b-specdec"' in src


def test_free_openrouter_models_in_brain_default_tiers():
    src = Path("agents/brain.py").read_text()
    assert '"openrouter/stepfun/step-3.5-flash:free"' in src
    assert '"openrouter/qwen/qwen3-next-80b-a3b-instruct:free"' in src
    assert '"openrouter/openai/gpt-oss-120b:free"' in src
    assert '"openrouter/meta-llama/llama-3.3-70b-instruct:free"' in src
    assert '"openrouter/arcee-ai/trinity-large-preview:free"' in src


def test_deepseek_v3_removed_from_brain_default_tiers():
    src = Path("agents/brain.py").read_text()
    default_tiers_section = src.split("DEFAULT_MODEL_TIERS", 1)[1].split("PARAM_ALIASES", 1)[0]
    assert '"openrouter/deepseek/deepseek-v3"' not in default_tiers_section


def test_stale_deepseek_v3_id_is_aliased_to_chat():
    src = Path("agents/brain.py").read_text()
    assert '"openrouter/deepseek/deepseek-v3": "openrouter/deepseek/deepseek-chat"' in src
    assert '"deepseek/deepseek-v3": "deepseek/deepseek-chat"' in src


def test_review_strategy_uses_gemini_pro():
    src = Path("agents/brain.py").read_text()
    assert 'override_model="gemini/gemini-2.5-pro"' in src


def test_override_model_param_exists():
    src = Path("agents/brain.py").read_text()
    assert "override_model: str | None = None" in src


def test_decommissioned_model_removed_from_runtime_config():
    cfg = Path("config/config.yaml").read_text()
    assert "groq/gemma2-9b-it" not in cfg


def test_decommissioned_model_marked_unavailable_in_agent_logic():
    src = Path("agents/brain.py").read_text()
    assert '"model_decommissioned"' in src
    assert '"decommissioned"' in src
    assert '"is not a valid model id"' in src
    assert '"invalid model id"' in src


def test_invalid_model_id_errors_are_listed_in_unavailable_matchers():
    tree = ast.parse(Path("agents/brain.py").read_text())

    method = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "TradingAgent":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "_is_unavailable_model_error":
                    method = item
                    break

    if method is None:
        raise AssertionError("Could not locate TradingAgent._is_unavailable_model_error")

    values = [
        node.value
        for node in ast.walk(method)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]

    assert "is not a valid model id" in values
    assert "invalid model id" in values


def test_parse_model_identifier_normalizes_stale_deepseek_v3_alias():
    tree = ast.parse(Path("agents/brain.py").read_text())

    aliases = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "MODEL_ID_ALIASES":
                    aliases = ast.literal_eval(node.value)
                    break
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "MODEL_ID_ALIASES":
                aliases = ast.literal_eval(node.value)
                break

    assert aliases is not None
    assert aliases["openrouter/deepseek/deepseek-v3"] == "openrouter/deepseek/deepseek-chat"
    assert aliases["deepseek/deepseek-v3"] == "deepseek/deepseek-chat"


# ─── Replay engine assertions ───────────────────────────────────────────────

def test_replay_uses_per_provider_delay():
    src = Path("core/replay_engine.py").read_text()
    assert "REPLAY_AI_CALL_DELAY_BY_PROVIDER" in src
    assert "REPLAY_AI_CALL_DELAY_SECONDS" not in src, (
        "Old fixed delay constant should be replaced by per-provider dict"
    )


def test_replay_delay_values():
    src = Path("core/replay_engine.py").read_text()
    # Verify the expected provider delays are present
    assert '"gemini"' in src
    assert '"groq"' in src
    assert '"openrouter"' in src
    assert '"default"' in src


# ─── No sub-32B models in chain ──────────────────────────────────────────────

def test_no_small_models_in_fallback():
    cfg = _active_config_text()
    banned = [
        "llama-3.1-8b",
        "mixtral-8x7b",
        "mistral-7b",
        "mistral-nemo",
        "qwen-2.5-7b",
        "gemini-1.5-flash",
        "mimo-v2-flash",
    ]
    for model in banned:
        assert model not in cfg, f"Banned model '{model}' should not be in config"


def test_trading_agent_has_fail_fast_latency_controls_in_config():
    cfg = Path("config/config.yaml").read_text()
    assert "provider_timeout_seconds:" in cfg
    assert "decision_timeout_seconds:" in cfg
    assert "max_fallback_wait_seconds:" in cfg
    assert "max_models_per_decision:" in cfg


def test_replay_uses_dedicated_latency_first_model_policy():
    cfg = Path("config/config.yaml").read_text()
    replay = Path("core/replay_engine.py").read_text()
    assert "replay_fallback_models:" in cfg
    assert 'agent_cfg["fallback_models"] = replay_fallbacks' in replay
    assert 'replay_max_models_per_decision' in replay
    assert 'replay_decision_timeout_seconds' in replay


def test_explicit_fallback_models_are_applied_before_tiers():
    src = Path("agents/brain.py").read_text()
    explicit_block = """explicit_fallbacks = config.get("fallback_models", []) or []
        for model in explicit_fallbacks:"""
    tier_block = """tiered = config.get("model_tiers", self.DEFAULT_MODEL_TIERS) or {}
        for models in tiered.values():"""
    assert explicit_block in src
    assert tier_block in src
    assert src.index(explicit_block) < src.index(tier_block)


def test_replay_max_fallback_wait_seconds_prefers_replay_section():
    src = Path("core/replay_engine.py").read_text()
    expected = """agent_cfg["max_fallback_wait_seconds"] = replay_cfg.get(
            "max_fallback_wait_seconds","""
    assert expected in src

def test_replay_no_longer_clears_model_cooldown_each_candle():
    replay = Path("core/replay_engine.py").read_text()
    assert "_model_consecutive_failures.clear()" not in replay
    assert "_model_skip_until.clear()" not in replay


def test_brain_uses_failure_count_based_backoff_with_budget():
    src = Path("agents/brain.py").read_text()
    assert "fail_count = self._model_consecutive_failures[model_id]" in src
    assert "remaining_budget = self.decision_timeout_seconds - elapsed" in src
    assert "all_models = all_models[: self.max_models_per_decision]" in src
    assert "timeout_seconds=min(self.provider_timeout_seconds, max(0.5, remaining_budget))" in src

def test_brain_classifies_timeout_errors_explicitly():
    src = Path("agents/brain.py").read_text()
    assert "def _is_timeout_error" in src
    assert '"read operation timed out"' in src
    assert '"read timed out"' in src


def test_unavailable_model_errors_now_trip_circuit_breaker():
    src = Path("agents/brain.py").read_text()
    assert "circuit_breaker_tripped_unavailable" in src
    assert "unavailable/permission failures" in src
    assert "self._model_skip_until[model_id]" in src


def test_replay_defaults_try_more_models_and_prioritise_provider_diversity():
    cfg = Path("config/config.yaml").read_text()
    assert "replay_max_models_per_decision: 5" in cfg
    assert "max_models_per_decision: 5" in cfg
    replay_block = cfg.split("replay_fallback_models:", 1)[1].split("replay_decision_timeout_seconds:", 1)[0]
    assert replay_block.index('    - "openrouter/stepfun/step-3.5-flash:free"') < replay_block.index(
        '    - "groq/llama-3.1-70b-versatile"'
    )


def test_last_model_preserves_timeout_and_rate_limit_reason():
    src = Path("agents/brain.py").read_text()
    timeout_block = """if self._is_timeout_error(e):
                    logger.warning(
                        "Model %s timed out after %.1fs; trying fallback.",
                        model_id,
                        min(self.provider_timeout_seconds, max(0.5, remaining_budget)),
                    )
                    reason = "timeout"
                    failure_reasons.append(f"{model_id}={reason}")
                    if is_last:
                        break"""
    rate_limit_break = """reason = "rate_limited"
                    failure_reasons.append(f"{model_id}={reason}")
                    if is_last:
                        break"""
    assert timeout_block in src
    assert rate_limit_break in src

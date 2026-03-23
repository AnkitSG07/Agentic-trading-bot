"""Tests for the AI model chain configuration after the overhaul."""

from pathlib import Path


# ─── Config file assertions ──────────────────────────────────────────────────

def test_xiaomimimo_removed_from_config():
    cfg = Path("config/config.yaml").read_text()
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


def test_deepseek_v3_in_config():
    cfg = Path("config/config.yaml").read_text()
    assert "openrouter/deepseek/deepseek-v3" in cfg


def test_groq_70b_models_in_brain_default_tiers():
    src = Path("agents/brain.py").read_text()
    assert '"groq/llama-3.1-70b-versatile"' in src
    assert '"groq/llama-3.3-70b-specdec"' in src


def test_deepseek_v3_in_brain_default_tiers():
    src = Path("agents/brain.py").read_text()
    assert '"openrouter/deepseek/deepseek-v3"' in src


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
    cfg = Path("config/config.yaml").read_text()
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

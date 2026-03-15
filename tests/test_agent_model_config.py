from pathlib import Path


def test_decommissioned_model_removed_from_runtime_config():
    cfg = Path("config/config.yaml").read_text()
    assert "groq/gemma2-9b-it" not in cfg


def test_decommissioned_model_marked_unavailable_in_agent_logic():
    src = Path("agents/brain.py").read_text()
    assert '"model_decommissioned"' in src
    assert '"decommissioned"' in src

from pathlib import Path


def test_default_task_routes_include_nemotron_candidate_eval():
    src = Path("agents/brain.py").read_text()
    assert '"candidate_eval"' in src
    assert '"openrouter/nvidia/nemotron-3-super-120b-a12b:free"' in src


def test_task_route_helper_is_present():
    src = Path("agents/brain.py").read_text()
    assert "def _task_route(" in src
    assert "self.task_model_routes" in src


def test_config_task_routes_include_deterministic_split():
    cfg = Path("config/config.yaml").read_text()
    assert "task_model_routes:" in cfg
    assert "candidate_eval:" in cfg
    assert "strategy_review:" in cfg
    assert "health_check:" in cfg
    assert "position_explain:" in cfg

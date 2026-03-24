from config.loader import load_config


def test_config_loader_applies_autonomous_defaults(monkeypatch):
    load_config.cache_clear()
    monkeypatch.setattr("config.loader.yaml.safe_load", lambda _fh: {"app": {"name": "TestApp"}})

    cfg = load_config()

    assert cfg["app"]["name"] == "TestApp"
    assert cfg["session"]["allow_exits_during_entry_blocks"] is True
    assert cfg["risk"]["min_risk_reward"] == 1.5
    assert cfg["risk"]["correlation_cap"] == 2
    assert cfg["risk"]["long_bias_cap"] == 10
    assert cfg["risk"]["short_bias_cap"] == 10
    assert cfg["risk"]["strategy_family_cap"] == 0.5
    assert cfg["engine"]["pause_on_mismatch"] is False
    assert cfg["engine"]["health_check_interval_seconds"] == 60
    assert cfg["engine"]["reconciliation_interval_seconds"] == 120
    assert cfg["replay"]["stop_target_precedence"] == "stop_first"
    assert cfg["news"]["confidence_modifier_cap"] == 0.2

    load_config.cache_clear()

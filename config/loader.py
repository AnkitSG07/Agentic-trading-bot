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
load_dotenv(Path(__file__).parent / ".env", override=False)


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
    return _expand(raw)

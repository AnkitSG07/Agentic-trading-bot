"""Quick verification of the model chain overhaul."""
import os, sys
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path

cfg = Path("config/config.yaml").read_text()
src = Path("agents/brain.py").read_text()
replay = Path("core/replay_engine.py").read_text()

# Extract non-comment lines for checking active model entries
cfg_active = "\n".join(l for l in cfg.splitlines() if not l.strip().startswith("#"))

passed = 0
failed = 0

def check(name, condition):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        print(f"  FAIL: {name}")

print("=== Model Chain Overhaul Verification ===\n")

print("[Step 1] xiaomimimo removal")
check("not in config.yaml (active)", "xiaomimimo" not in cfg_active and "XIAOMI_MIMO_API_KEY" not in cfg_active)
check("not in brain.py (attr)", "xiaomi_mimo_api_key" not in src)
check("not in brain.py (model string)", '"xiaomimimo/mimo-v2-flash"' not in src)
check("not in brain.py (api url)", "api.xiaomimimo.com" not in src)

print("\n[Step 2] New models added")
check("groq/llama-3.1-70b-versatile in config", "groq/llama-3.1-70b-versatile" in cfg)
check("groq/llama-3.3-70b-specdec in config", "groq/llama-3.3-70b-specdec" in cfg)
check("deepseek-v3 in config", "openrouter/deepseek/deepseek-v3" in cfg)
check("groq 70b in brain.py", '"groq/llama-3.1-70b-versatile"' in src and '"groq/llama-3.3-70b-specdec"' in src)
check("deepseek-v3 in brain.py", '"openrouter/deepseek/deepseek-v3"' in src)
check("override_model param", "override_model: str | None = None" in src)
check("review_strategy uses gemini-2.5-pro", 'override_model="gemini/gemini-2.5-pro"' in src)

print("\n[Step 3] Replay adaptive delay")
check("REPLAY_AI_CALL_DELAY_BY_PROVIDER exists", "REPLAY_AI_CALL_DELAY_BY_PROVIDER" in replay)
check("old REPLAY_AI_CALL_DELAY_SECONDS removed", "REPLAY_AI_CALL_DELAY_SECONDS" not in replay)

print("\n[Step 4] No banned models in active config lines")
banned = ["llama-3.1-8b", "mixtral-8x7b", "mistral-7b", "mistral-nemo", "qwen-2.5-7b", "gemini-1.5-flash", "mimo-v2-flash"]
for m in banned:
    check(f"'{m}' not active in config", m not in cfg_active)

print(f"\n=== TOTAL: {passed} passed, {failed} failed ===")
sys.exit(1 if failed else 0)

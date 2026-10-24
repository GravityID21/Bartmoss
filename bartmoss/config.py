"""Configuration: built-in defaults, merged with user/project JSON files, env vars and CLI flags.

Order (later wins): DEFAULTS -> ~/.config/bartmoss/config.json -> ./bartmoss.json -> env -> CLI flags.
API keys are only ever read from the environment (or a git-ignored .env file).
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

DEFAULTS: dict = {
    # Haiku 5.5 led or tied every evaluated mode (evals/LEADERBOARD.md); it's also the /ask default
    # until /ask has its own eval (SPEC P2.14).
    "default_model": "anthropic:claude-haiku-5-5",
    "modes": {
        "ask": {"model": None, "max_tokens": 4000, "effort": "low", "web_search_max_uses": 3},
        # Measured defaults (P2.7, evals/LEADERBOARD.md): Haiku 5.5 was best or within 0.05 of best in every
        # evaluated mode, at 1/5 to 1/13 of Sonnet 5.5's cost. /ask has no eval yet and uses default_model.
        "debug": {"model": "anthropic:claude-haiku-5-5", "max_tokens": 32000, "effort": "medium", "max_retries": 2},
        "tb": {"model": "anthropic:claude-haiku-5-5", "max_tokens": 32000, "effort": "medium", "max_retries": 2},
        "vplan": {"model": "anthropic:claude-haiku-5-5", "max_tokens": 16000, "effort": "medium", "max_retries": 1},
        "uvmplan": {"model": "anthropic:claude-haiku-5-5", "max_tokens": 16000, "effort": "medium", "max_retries": 1},
    },
    # Providers other than "anthropic" and "mock" are OpenAI-compatible endpoints.
    # Check each provider's docs for the current base URL and model names.
    "providers": {
        "anthropic": {"type": "anthropic", "refusal_fallback": True},
        "openai": {
            "type": "openai_compat",
            "base_url": None,
            "api_key_env": "OPENAI_API_KEY",
            "max_tokens_param": "max_completion_tokens",
        },
        "ollama": {"type": "openai_compat", "base_url": "http://localhost:11434/v1", "api_key_env": None},
        "deepseek": {"type": "openai_compat", "base_url": "https://api.deepseek.com", "api_key_env": "DEEPSEEK_API_KEY"},
        "openrouter": {"type": "openai_compat", "base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"},
        "gemini": {
            "type": "openai_compat",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "api_key_env": "GEMINI_API_KEY",
        },
        "mock": {"type": "mock"},
    },
    # USD per million tokens: [input, output]. Used only for the /usage estimate.
    # Claude prices as of Oct 2026; add your own entries for other providers.
    "prices": {
        "anthropic:claude-opus-5-5": [4.0, 20.0],
        "anthropic:claude-sonnet-5-5": [2.0, 10.0],
        "anthropic:claude-haiku-5-5": [0.10, 0.50],
        "anthropic:claude-fable-5-1": [10.0, 50.0],
    },
    # Optional /ask pre-filter (P2.4): a cheap model labels questions before the main model sees them.
    "filter": {"enabled": False, "model": "anthropic:claude-haiku-5-5", "max_tokens": 1024},
    # Judge for vplan/uvmplan evals (rubric grading). Sonnet by default: in a spot check Haiku flipped
    # verdicts between runs on the same plan, which would make prompt A/B comparisons meaningless.
    "eval": {"judge_model": "anthropic:claude-sonnet-5-5", "judge_max_tokens": 8000},
    "simulator": "auto",  # auto | verilator | iverilog
    "output_dir": "bartmoss_out",
    "sim_timeout_s": 300,
}

USER_CONFIG = Path.home() / ".config" / "bartmoss" / "config.json"
USER_ENV = Path.home() / ".config" / "bartmoss" / ".env"
PROJECT_CONFIG = Path("bartmoss.json")


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_dotenv(path: Path) -> None:
    """Minimal .env reader: KEY=VALUE lines, never overrides variables already set."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


def load_config(root: Path | None = None) -> dict:
    root = root or Path.cwd()
    load_dotenv(root / ".env")
    load_dotenv(USER_ENV)  # lets keys work from any directory; the workspace .env wins
    config = copy.deepcopy(DEFAULTS)
    for path in (USER_CONFIG, root / PROJECT_CONFIG):
        if path.is_file():
            try:
                config = deep_merge(config, json.loads(path.read_text()))
            except json.JSONDecodeError as e:
                raise SystemExit(f"Invalid JSON in {path}: {e}")
    if os.environ.get("BARTMOSS_MODEL"):
        config["default_model"] = os.environ["BARTMOSS_MODEL"]
    return config


def parse_model_spec(spec: str) -> tuple[str, str]:
    """'provider:model' -> (provider, model). Splits on the first colon only,
    so Ollama tags like 'ollama:qwen2.5-coder:14b' keep their own colon."""
    if ":" not in spec:
        raise ValueError(f"Model must look like provider:model, got {spec!r}")
    provider, model = spec.split(":", 1)
    if not provider or not model:
        raise ValueError(f"Model must look like provider:model, got {spec!r}")
    return provider, model


def model_for_mode(config: dict, mode: str, override: str | None = None) -> str:
    return override or config["modes"].get(mode, {}).get("model") or config["default_model"]

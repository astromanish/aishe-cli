"""Configuration management — YAML config file with env override support."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


# ─── Platform paths ─────────────────────────────────────────

def _default_data_dir() -> str:
    """Data directory — always `~/aishe` on every platform."""
    return str(Path.home() / "aishe")


def _default_config_dir() -> Path:
    """Platform-appropriate config directory."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "aishe"
    return Path.home() / ".config" / "aishe"


CONFIG_DIR = _default_config_dir()
CONFIG_FILE = CONFIG_DIR / "config.yaml"


# ─── Defaults ───────────────────────────────────────────────

DEFAULT_CONFIG: Dict[str, Any] = {
    "model": "deepseek-v4-flash",
    "provider": "",  # sarvam | ollama-cloud | local | openrouter | opencode-go
    "providers": {
        "sarvam": {
            "base_url": "https://api.sarvam.ai/v1",
            "api_key": "",
        },
        "ollama-cloud": {
            "base_url": "http://localhost:11434",
            "api_key": "",
        },
        "local": {
            "base_url": "http://localhost:8080",
            "api_key": "",
        },
        "openrouter": {
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "",
        },
        "opencode-go": {
            "base_url": "https://opencode.ai/zen/go/v1",
            "api_key": "",
        },
    },
    "services": {
        "deepagent": "http://localhost:8765",
        "ollama": "http://localhost:11434",
        "stt": "http://localhost:5093",
        "tts": "http://localhost:8766",
    },
    "voice": {
        "default_voice": "F4",
        "recording_duration": 5,
        "vad_enabled": True,
        "vad_threshold": 0.5,
        "vad_min_speech_duration_ms": 250,
        "vad_min_silence_duration_ms": 500,
    },
    "data": {
        "dir": _default_data_dir(),
    },
    "telegram": {
        "token": "",
        "allowed_users": [],
        "allow_all": False,
        "log_file": str(Path.home() / "aishe" / "telegram.log"),
    },
    "ui": {
        "color": True,
    },
}


def get_config_path() -> Path:
    return CONFIG_FILE


def load() -> Dict[str, Any]:
    """Load config, merging defaults with user config."""
    cfg = dict(DEFAULT_CONFIG)  # shallow copy top-level

    if not CONFIG_FILE.exists():
        return cfg

    if yaml is None:
        print("Warning: PyYAML not installed. Run: pip install pyyaml", file=sys.stderr)
        return cfg

    try:
        with open(CONFIG_FILE) as f:
            user_cfg = yaml.safe_load(f) or {}
        _deep_merge(cfg, user_cfg)
    except Exception as e:
        print(f"Warning: Failed to load config: {e}", file=sys.stderr)

    return cfg


def save(cfg: Dict[str, Any]) -> None:
    """Save config to file."""
    if yaml is None:
        print("Error: PyYAML not installed. Run: pip install pyyaml", file=sys.stderr)
        sys.exit(1)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)


def _deep_merge(base: Dict, override: Dict) -> None:
    """Recursively merge override into base."""
    for key, val in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(val, dict):
            _deep_merge(base[key], val)
        else:
            base[key] = val


def get(key: str, default: Any = None) -> Any:
    """Get a dot-separated config value, e.g. get('services.ollama')."""
    cfg = load()
    parts = key.split(".")
    val: Any = cfg
    for part in parts:
        if isinstance(val, dict):
            val = val.get(part)
        else:
            return default
        if val is None:
            return default
    return val


def set_key(key: str, value: str) -> None:
    """Set a dot-separated config key, inferring types from defaults."""
    cfg = load()
    parts = key.split(".")
    target = cfg
    for part in parts[:-1]:
        target = target.setdefault(part, {})
    key_name = parts[-1]

    # Try to coerce to the type of the default value
    default_val = _deep_get(DEFAULT_CONFIG, parts)
    if default_val is not None:
        if isinstance(default_val, bool):
            target[key_name] = value.lower() in ("true", "yes", "1")
        elif isinstance(default_val, int):
            target[key_name] = int(value)
        elif isinstance(default_val, float):
            target[key_name] = float(value)
        else:
            target[key_name] = value
    else:
        target[key_name] = value

    save(cfg)


def _deep_get(d: Dict, parts: list) -> Any:
    val: Any = d
    for p in parts:
        if isinstance(val, dict):
            val = val.get(p)
        else:
            return None
    return val


def cmd_config(args: Any) -> None:
    """Handle `aishe config` subcommand."""
    cfg = load()

    if args.get:  # type: ignore
        val = get(args.get)  # type: ignore
        if val is None:
            print(f"Key '{args.get}' not found")  # type: ignore
            sys.exit(1)
        print(val)
        return

    if args.set:  # type: ignore
        if not args.value:  # type: ignore
            print("Usage: aishe config set <key> <value>")
            sys.exit(1)
        set_key(args.set, args.value)  # type: ignore
        print(f"Set {args.set} = {args.value}")  # type: ignore
        return

    # Print all
    if yaml is None:
        print("Error: PyYAML not installed. Run: pip install pyyaml")
        sys.exit(1)
    print(yaml.dump(cfg, default_flow_style=False, sort_keys=False).strip())

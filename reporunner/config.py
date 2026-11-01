"""Configuration handling for RepoRunner CLI.

Stores non-sensitive configuration in a JSON file under the user's home directory (~/.reporunner/config.json).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

CONFIG_DIR = Path.home() / ".reporunner"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "backend_url": "http://localhost:8000",
    "default_repo": "",
    "default_task": "",
    "default_branch": "main",
    "mock_mode": False,
}


def load_config() -> Dict[str, Any]:
    """Load configuration from the config file, creating defaults if missing."""
    if not CONFIG_DIR.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = DEFAULT_CONFIG.copy()
        merged.update(data)
        return merged
    except Exception:
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()


def save_config(config: Dict[str, Any]) -> None:
    """Persist configuration to disk, preserving only non-secret values."""
    if not CONFIG_DIR.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def update_config(**kwargs: Any) -> Dict[str, Any]:
    """Update specific keys in the config and persist the file."""
    cfg = load_config()
    cfg.update(kwargs)
    save_config(cfg)
    return cfg

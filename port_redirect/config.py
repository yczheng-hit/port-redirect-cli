"""JSON configuration and state management for port-redirect."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

STATE_DIR = Path.home() / ".port_redirect"
STATE_FILE = STATE_DIR / "state.json"
CONFIG_FILE = STATE_DIR / "config.json"
LOG_DIR = STATE_DIR / "logs"


def _ensure_dirs():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


# ── State management (running proxies) ──

def load_state() -> dict:
    """Load state from JSON file. Returns default structure if file missing."""
    _ensure_dirs()
    if not STATE_FILE.exists():
        return {"proxies": {}}
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"proxies": {}}


def save_state(state: dict):
    """Save state to JSON file atomically."""
    _ensure_dirs()
    tmp = STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    tmp.replace(STATE_FILE)


def add_proxy(name: str, listen_port: int, target_host: str, target_port: int, pid: int):
    """Register a proxy in the state file."""
    state = load_state()
    state["proxies"][name] = {
        "listen_port": listen_port,
        "target_host": target_host,
        "target_port": target_port,
        "pid": pid,
        "status": "running",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    save_state(state)


def remove_proxy(name: str) -> bool:
    """Remove a proxy from state. Returns True if existed."""
    state = load_state()
    existed = state["proxies"].pop(name, None) is not None
    if existed:
        save_state(state)
    return existed


def update_status(name: str, status: str):
    """Update status of a proxy (running/stopped/error)."""
    state = load_state()
    if name in state["proxies"]:
        state["proxies"][name]["status"] = status
        save_state(state)


def list_proxies() -> dict:
    """Return all proxies from state."""
    return load_state()["proxies"]


def get_proxy(name: str) -> dict | None:
    """Get a single proxy by name."""
    return load_state()["proxies"].get(name)


# ── Config file management (proxy definitions) ──

def load_config(path: str | None = None) -> dict:
    """Load proxy config from a JSON file.

    If path is None, try the default location (~/.port_redirect/config.json).
    Returns dict with a 'proxies' list.
    """
    config_path = Path(path) if path else CONFIG_FILE
    if not config_path.exists():
        return {"proxies": []}
    try:
        with open(config_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"proxies": []}


def validate_config_entry(entry: dict) -> str | None:
    """Validate a single config entry. Returns error message or None."""
    required = ["listen_port", "target_host", "target_port"]
    for key in required:
        if key not in entry:
            return f"Missing required field: '{key}'"

    if not isinstance(entry["listen_port"], int) or not (1 <= entry["listen_port"] <= 65535):
        return "listen_port must be an integer between 1 and 65535"

    if not isinstance(entry["target_port"], int) or not (1 <= entry["target_port"] <= 65535):
        return "target_port must be an integer between 1 and 65535"

    if not isinstance(entry["target_host"], str) or not entry["target_host"]:
        return "target_host must be a non-empty string"

    return None
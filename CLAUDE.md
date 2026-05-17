# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`port-redirect` is a TCP port forwarding CLI tool. It listens on a local port and forwards all TCP traffic to a target IP:port. Supports foreground and background (daemon) modes, JSON config file batch loading, and process lifecycle management.

## Architecture

```
port_redirect/
├── cli.py       # argparse CLI entry point + subcommand handlers
├── proxy.py     # asyncio TCP proxy engine (ProxyServer class)
├── daemon.py    # Double-fork daemonization + PID management
├── config.py    # JSON state/config file read/write
├── __init__.py  # Version string
└── __main__.py  # python -m port_redirect support
```

- **proxy.py**: `ProxyServer` uses `asyncio.start_server` to listen on `0.0.0.0:<port>`. Each connection spawns two relay tasks (local→remote, remote→local) using `asyncio.wait(FIRST_COMPLETED)`. Connections to target have a 30s timeout.
- **daemon.py**: Double-fork (`os.fork()` × 2) to detach from terminal. PID files in `~/.port_redirect/<name>.pid`. Logs to `~/.port_redirect/logs/<name>.log` with rotation (10MB × 3). Stop uses SIGTERM then SIGKILL if needed.
- **config.py**: State persisted in `~/.port_redirect/state.json` (running proxies with PID/status). Config file for batch start in `~/.port_redirect/config.json`. Atomic writes via `.tmp` + replace.
- **cli.py**: Subcommands — start, stop, list, restart, logs, apply.

## CLI Commands

```bash
# Install (dev)
uv venv --python 3.12 && source .venv/bin/activate && uv pip install -e .

# Start a proxy
port-redirect start <listen_port> <target_host> <target_port> [--name NAME] [--daemon] [--log-level LEVEL]

# Stop / Restart
port-redirect stop <name>
port-redirect restart <name>

# List running proxies
port-redirect list

# View logs
port-redirect logs <name> [--tail N]

# Batch start from JSON config
port-redirect apply [--config path/to/config.json] [--daemon]
```

## Config File Format (~/.port_redirect/config.json)

```json
{
  "proxies": [
    {
      "name": "my-proxy",
      "listen_port": 8080,
      "target_host": "10.0.0.1",
      "target_port": 80,
      "daemon": true,
      "log_level": "INFO"
    }
  ]
}
```

## Key Design Notes

- **No external dependencies** — pure Python 3.8+ stdlib (asyncio, argparse, logging).
- **State directory**: `~/.port_redirect/` — not in repo, created on first use.
- **Daemon lifecycle**: double-fork → setsid → stdio to /dev/null → file logging. PID tracked in state.json + .pid file for reliable stop/restart.
- **`apply` command**: forks once per config entry, each child runs its own asyncio event loop. This enables batch starting multiple proxies from a single command.
- **Port conflict detection**: checks both state.json (named proxies) and kernel bind before starting.
- **Orphan detection**: `list` checks if PID is alive; marks as "stopped (orphaned)" if the process died without cleanup.
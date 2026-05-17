"""CLI entry point for port-redirect."""

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from port_redirect import __version__
from port_redirect.config import list_proxies, get_proxy, add_proxy, remove_proxy, update_status, load_config, validate_config_entry
from port_redirect.proxy import ProxyServer
from port_redirect.daemon import daemonize, write_pid, read_pid, remove_pid, stop_daemon, is_running, setup_logger
from port_redirect.diagnose import diagnose_proxy


def cmd_start(args: argparse.Namespace):
    """Start a port forward proxy."""
    name = args.name or f"proxy-{args.listen_port}"

    # Check for port conflicts
    existing = list_proxies()
    if name in existing:
        print(f"Error: proxy '{name}' already exists. Stop it first or use a different name.", file=sys.stderr)
        sys.exit(1)

    for pname, pdata in existing.items():
        if pdata.get("listen_port") == args.listen_port and pdata.get("status") == "running":
            print(f"Error: port {args.listen_port} is already in use by proxy '{pname}'.", file=sys.stderr)
            sys.exit(1)

    print(f"Starting proxy '{name}': 0.0.0.0:{args.listen_port} -> {args.target_host}:{args.target_port}")

    if args.daemon:
        # Background mode
        logger = daemonize(name, args.log_level)

        proxy = ProxyServer(args.listen_port, args.target_host, args.target_port)

        async def run():
            await proxy.start()
            add_proxy(name, args.listen_port, args.target_host, args.target_port, os.getpid())
            write_pid(name, os.getpid())
            logger.info("Proxy started (daemon): 0.0.0.0:%s -> %s:%s [pid=%s]",
                        args.listen_port, args.target_host, args.target_port, os.getpid())
            await proxy.serve_forever()

        try:
            asyncio.run(run())
        except KeyboardInterrupt:
            asyncio.run(proxy.stop())
            remove_pid(name)
            remove_proxy(name)
            logger.info("Proxy stopped (daemon)")
    else:
        # Foreground mode
        logger = setup_logger(name, args.log_level)
        # Also log to console in foreground
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(console)

        proxy = ProxyServer(args.listen_port, args.target_host, args.target_port)

        async def run():
            await proxy.start()
            add_proxy(name, args.listen_port, args.target_host, args.target_port, os.getpid())
            write_pid(name, os.getpid())
            print(f"Proxy '{name}' running in foreground. Press Ctrl+C to stop.")
            await proxy.serve_forever()

        try:
            asyncio.run(run())
        except KeyboardInterrupt:
            print("\nShutting down...")
            asyncio.run(proxy.stop())
            remove_pid(name)
            remove_proxy(name)
            print("Proxy stopped.")


def cmd_stop(args: argparse.Namespace):
    """Stop a running proxy."""
    name = args.name

    proxy = get_proxy(name)
    if proxy is None:
        print(f"Error: proxy '{name}' not found.", file=sys.stderr)
        sys.exit(1)

    if stop_daemon(name):
        print(f"Proxy '{name}' stopped.")
    else:
        print(f"Error: could not stop proxy '{name}'.", file=sys.stderr)
        sys.exit(1)


def cmd_list(args: argparse.Namespace):
    """List all proxies with their status."""
    proxies = list_proxies()
    if not proxies:
        print("No proxies configured.")
        return

    print(f"{'Name':<20} {'Local':<18} {'Target':<22} {'PID':<8} {'Status':<10} {'Created'}")
    print("-" * 100)
    for name, data in sorted(proxies.items()):
        local = f"0.0.0.0:{data['listen_port']}"
        target = f"{data['target_host']}:{data['target_port']}"
        pid = str(data.get("pid", "?"))
        status = data.get("status", "unknown")

        # Check if pid is actually alive
        if status == "running" and data.get("pid"):
            if not is_running(data["pid"]):
                status = "stopped (orphaned)"

        created = data.get("created_at", "")[:19].replace("T", " ")
        print(f"{name:<20} {local:<18} {target:<22} {pid:<8} {status:<10} {created}")


def cmd_restart(args: argparse.Namespace):
    """Restart a proxy."""
    name = args.name

    proxy = get_proxy(name)
    if proxy is None:
        print(f"Error: proxy '{name}' not found.", file=sys.stderr)
        sys.exit(1)

    print(f"Restarting proxy '{name}'...")
    stop_daemon(name)
    # Rebuild args for start
    start_args = argparse.Namespace(
        listen_port=proxy["listen_port"],
        target_host=proxy["target_host"],
        target_port=proxy["target_port"],
        name=name,
        daemon=True,
        log_level="INFO",
    )
    cmd_start(start_args)


def _start_one_proxy(listen_port: int, target_host: int, target_port: int, name: str, daemon: bool, log_level: str):
    """Start a single proxy — used by apply for each config entry."""
    if daemon:
        logger = daemonize(name, log_level)
        proxy = ProxyServer(listen_port, target_host, target_port)

        async def run():
            await proxy.start()
            add_proxy(name, listen_port, target_host, target_port, os.getpid())
            write_pid(name, os.getpid())
            logger.info("Proxy started (daemon): 0.0.0.0:%s -> %s:%s [pid=%s]",
                        listen_port, target_host, target_port, os.getpid())
            await proxy.serve_forever()

        try:
            asyncio.run(run())
        except KeyboardInterrupt:
            asyncio.run(proxy.stop())
            remove_pid(name)
            remove_proxy(name)
    else:
        proxy = ProxyServer(listen_port, target_host, target_port)

        async def run():
            await proxy.start()
            add_proxy(name, listen_port, target_host, target_port, os.getpid())
            write_pid(name, os.getpid())
            print(f"Proxy '{name}' running in foreground. Press Ctrl+C to stop.")
            await proxy.serve_forever()

        try:
            asyncio.run(run())
        except KeyboardInterrupt:
            print("\nShutting down...")
            asyncio.run(proxy.stop())
            remove_pid(name)
            remove_proxy(name)


def cmd_apply(args: argparse.Namespace):
    """Start proxies from a JSON config file."""
    config = load_config(args.config)
    entries = config.get("proxies", [])
    if not entries:
        print(f"No proxies defined in config file: {args.config}", file=sys.stderr)
        sys.exit(1)

    errors = 0
    for entry in entries:
        err = validate_config_entry(entry)
        if err:
            print(f"Config error: {err} — entry: {entry}", file=sys.stderr)
            errors += 1
            continue

        name = entry.get("name", f"proxy-{entry['listen_port']}")
        listen_port = entry["listen_port"]
        target_host = entry["target_host"]
        target_port = entry["target_port"]
        daemon = entry.get("daemon", args.daemon)
        log_level = entry.get("log_level", "INFO")

        # Check conflicts
        existing = list_proxies()
        if name in existing:
            print(f"Warning: proxy '{name}' already exists, skipping.", file=sys.stderr)
            continue

        conflict = False
        for pname, pdata in existing.items():
            if pdata.get("listen_port") == listen_port and pdata.get("status") == "running":
                print(f"Warning: port {listen_port} already in use by '{pname}', skipping.", file=sys.stderr)
                conflict = True
                break
        if conflict:
            continue

        print(f"Starting proxy '{name}': 0.0.0.0:{listen_port} -> {target_host}:{target_port}")

        if daemon:
            pid = os.fork()
            if pid == 0:
                _start_one_proxy(listen_port, target_host, target_port, name, True, log_level)
                os._exit(0)
            else:
                # Parent waits briefly to let child register
                import time
                time.sleep(0.5)
        else:
            _start_one_proxy(listen_port, target_host, target_port, name, False, log_level)

    if errors:
        sys.exit(1)


def cmd_diagnose(args: argparse.Namespace):
    """Run diagnostics on a proxy."""
    report = diagnose_proxy(args.name)
    print(report)


def cmd_logs(args: argparse.Namespace):
    """Show logs for a proxy."""
    name = args.name
    log_path = Path.home() / ".port_redirect" / "logs" / f"{name}.log"

    if not log_path.exists():
        print(f"Error: no logs found for proxy '{name}'.", file=sys.stderr)
        sys.exit(1)

    from pathlib import Path as _Path
    lines = _Path(log_path).read_text().splitlines()
    tail = lines[-args.tail:] if args.tail else lines
    for line in tail:
        print(line)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="port-redirect",
        description="TCP port forwarding CLI tool — forward local port traffic to a remote target.",
    )
    parser.add_argument("--version", action="version", version=f"port-redirect {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    # start
    p_start = sub.add_parser("start", help="Start a new port forward")
    p_start.add_argument("listen_port", type=int, help="Local port to listen on")
    p_start.add_argument("target_host", help="Target host (IP or domain)")
    p_start.add_argument("target_port", type=int, help="Target port")
    p_start.add_argument("--name", "-n", help="Name for this proxy (default: proxy-<port>)")
    p_start.add_argument("--daemon", "-d", action="store_true", help="Run in background")
    p_start.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                         help="Log level (default: INFO)")

    # stop
    p_stop = sub.add_parser("stop", help="Stop a running proxy")
    p_stop.add_argument("name", help="Name of the proxy to stop")

    # list
    sub.add_parser("list", help="List all proxies")

    # restart
    p_restart = sub.add_parser("restart", help="Restart a proxy")
    p_restart.add_argument("name", help="Name of the proxy to restart")

    # logs
    p_logs = sub.add_parser("logs", help="Show proxy logs")
    p_logs.add_argument("name", help="Name of the proxy")
    p_logs.add_argument("--tail", "-t", type=int, default=50, help="Number of lines to show (default: 50)")

    # apply
    p_apply = sub.add_parser("apply", help="Start proxies from a JSON config file")
    p_apply.add_argument("--config", "-c", default=str(Path.home() / ".port_redirect" / "config.json"),
                         help="Path to JSON config file (default: ~/.port_redirect/config.json)")
    p_apply.add_argument("--daemon", "-d", action="store_true", help="Run all in background")

    # diagnose
    p_diag = sub.add_parser("diagnose", help="Run connectivity and latency diagnostics on a proxy")
    p_diag.add_argument("name", help="Name of the proxy to diagnose")

    return parser


def main():
    """Main entry point."""
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "start":
        cmd_start(args)
    elif args.command == "stop":
        cmd_stop(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "restart":
        cmd_restart(args)
    elif args.command == "logs":
        cmd_logs(args)
    elif args.command == "apply":
        cmd_apply(args)
    elif args.command == "diagnose":
        cmd_diagnose(args)


if __name__ == "__main__":
    main()
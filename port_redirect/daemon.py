"""Daemon (background) process management for port-redirect."""

import os
import signal
import sys
import logging
import logging.handlers
from pathlib import Path

from port_redirect.config import STATE_DIR, LOG_DIR, add_proxy, remove_proxy, update_status


def setup_logger(name: str, log_level: str = "INFO") -> logging.Logger:
    """Configure file logging for a daemon proxy."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{name}.log"

    logger = logging.getLogger(f"port-redirect.daemon.{name}")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=10 * 1024 * 1024, backupCount=3
    )
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger


def daemonize(name: str, log_level: str = "INFO") -> logging.Logger:
    """Double-fork to detach from terminal. Returns logger for the child."""
    pid = os.fork()
    if pid > 0:
        # Parent exits — child continues
        os._exit(0)

    # Child: become session leader
    os.setsid()
    signal.signal(signal.SIGHUP, signal.SIG_IGN)

    pid2 = os.fork()
    if pid2 > 0:
        os._exit(0)

    # Second child: fully detached
    sys.stdout.flush()
    sys.stderr.flush()

    # Redirect stdio to /dev/null
    with open(os.devnull, "r") as devnull:
        os.dup2(devnull.fileno(), sys.stdin.fileno())
    with open(os.devnull, "w") as devnull:
        os.dup2(devnull.fileno(), sys.stdout.fileno())
        os.dup2(devnull.fileno(), sys.stderr.fileno())

    return setup_logger(name, log_level)


def write_pid(name: str, pid: int):
    """Write PID file for a named proxy."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    pid_path = STATE_DIR / f"{name}.pid"
    pid_path.write_text(str(pid))


def read_pid(name: str) -> int | None:
    """Read PID from file. Returns None if file missing or invalid."""
    pid_path = STATE_DIR / f"{name}.pid"
    if not pid_path.exists():
        return None
    try:
        return int(pid_path.read_text().strip())
    except (ValueError, OSError):
        return None


def remove_pid(name: str):
    """Remove PID file."""
    pid_path = STATE_DIR / f"{name}.pid"
    pid_path.unlink(missing_ok=True)


def is_running(pid: int) -> bool:
    """Check if a process with given PID is running."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def stop_daemon(name: str) -> bool:
    """Stop a daemon proxy by name. Returns True if stopped successfully."""
    pid = read_pid(name)
    if pid is None:
        return False

    if not is_running(pid):
        remove_pid(name)
        remove_proxy(name)
        return True

    try:
        os.kill(pid, signal.SIGTERM)
        # Give it a moment, then force kill
        import time
        time.sleep(0.5)
        if is_running(pid):
            os.kill(pid, signal.SIGKILL)
    except OSError:
        pass

    remove_pid(name)
    remove_proxy(name)
    return True
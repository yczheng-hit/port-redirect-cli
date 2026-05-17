"""Diagnostic tools for port-redirect — latency, connectivity, and path checks."""

import asyncio
import socket
import subprocess
import time
from datetime import datetime

from port_redirect.config import get_proxy


def dns_lookup(host: str) -> dict:
    """Resolve a hostname and measure resolution time."""
    start = time.perf_counter()
    try:
        addrs = socket.getaddrinfo(host, None)
        elapsed = (time.perf_counter() - start) * 1000
        ips = list(set(addr[4][0] for addr in addrs))
        return {"host": host, "ips": ips, "ms": round(elapsed, 1), "ok": True}
    except socket.gaierror as e:
        elapsed = (time.perf_counter() - start) * 1000
        return {"host": host, "ips": [], "ms": round(elapsed, 1), "ok": False, "error": str(e)}


async def _tcp_connect_one(host: str, port: int) -> float | None:
    """Attempt a single TCP connection and return handshake time in ms, or None."""
    start = time.perf_counter()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=10,
        )
        elapsed = (time.perf_counter() - start) * 1000
        writer.close()
        await writer.wait_closed()
        return elapsed
    except (OSError, asyncio.TimeoutError):
        return None


async def tcp_connect_latency(host: str, port: int, count: int = 3) -> dict:
    """Measure TCP handshake latency over multiple attempts."""
    times = []
    for i in range(count):
        t = await _tcp_connect_one(host, port)
        if t is not None:
            times.append(t)

    if not times:
        return {"ok": False, "avg_ms": None, "min_ms": None, "max_ms": None, "samples": 0}

    return {
        "ok": True,
        "avg_ms": round(sum(times) / len(times), 1),
        "min_ms": round(min(times), 1),
        "max_ms": round(max(times), 1),
        "samples": len(times),
    }


async def proxy_rtt(host: str, port: int, count: int = 3) -> dict:
    """Measure round-trip time through the proxy by connecting and sending data."""
    times = []
    for _ in range(count):
        start = time.perf_counter()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=10,
            )
            # Send a small payload and measure response
            writer.write(b"GET / HTTP/1.0\r\nHost: healthcheck\r\n\r\n")
            await writer.drain()
            await asyncio.wait_for(reader.read(1), timeout=10)
            elapsed = (time.perf_counter() - start) * 1000
            times.append(elapsed)
            writer.close()
            await writer.wait_closed()
        except (OSError, asyncio.TimeoutError):
            # For non-HTTP targets (e.g. SSH), just measure connect+close
            start2 = time.perf_counter()
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port),
                    timeout=10,
                )
                elapsed = (time.perf_counter() - start2) * 1000
                times.append(elapsed)
                writer.close()
                await writer.wait_closed()
            except (OSError, asyncio.TimeoutError):
                pass

    if not times:
        return {"ok": False, "avg_ms": None, "samples": 0}

    return {
        "ok": True,
        "avg_ms": round(sum(times) / len(times), 1),
        "min_ms": round(min(times), 1),
        "max_ms": round(max(times), 1),
        "samples": len(times),
    }


def check_tailscale(target_ip: str) -> dict:
    """Check Tailscale connection path to a target IP."""
    result = {"installed": False, "connected": False, "path": None, "relay": None, "direct": False}

    # Check if tailscale is installed
    try:
        subprocess.run(["tailscale", "version"], capture_output=True, timeout=5)
        result["installed"] = True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return result

    # Check tailscale status for this IP
    try:
        status = subprocess.run(
            ["tailscale", "status"],
            capture_output=True, text=True, timeout=5,
        )
        for line in status.stdout.splitlines():
            if target_ip in line:
                parts = line.split()
                if len(parts) >= 3:
                    result["connected"] = True
                    # Check for relay or direct
                    status_str = " ".join(parts[2:])
                    if "relay" in status_str:
                        result["relay"] = status_str
                        result["direct"] = False
                    elif "active" in status_str or "idle" in status_str:
                        result["direct"] = True
                        result["path"] = status_str
                    break
    except (subprocess.TimeoutExpired, OSError):
        pass

    # Try tailscale ping to check path
    if result["connected"]:
        try:
            ping = subprocess.run(
                ["tailscale", "ping", "--c", "2", "--until-direct=true", target_ip],
                capture_output=True, text=True, timeout=15,
            )
            for line in ping.stdout.splitlines():
                if "via" in line and "DERP" in line:
                    result["relay"] = line.strip()
                    result["direct"] = False
                elif "pong" in line and "via" not in line:
                    result["direct"] = True
                    result["path"] = line.strip()
                elif "direct connection not established" in line:
                    result["direct"] = False
        except (subprocess.TimeoutExpired, OSError):
            pass

    return result


def diagnose_proxy(name: str) -> str:
    """Run full diagnostics on a named proxy and return a formatted report."""
    proxy = get_proxy(name)
    if proxy is None:
        return f"Error: proxy '{name}' not found."

    listen_port = proxy["listen_port"]
    target_host = proxy["target_host"]
    target_port = proxy["target_port"]

    lines = []
    lines.append(f"Diagnosing proxy '{name}': 0.0.0.0:{listen_port} -> {target_host}:{target_port}")
    lines.append("")

    # 1. DNS resolution
    dns = dns_lookup(target_host)
    if dns["ok"]:
        lines.append(f"  DNS resolution:    {', '.join(dns['ips'])} ({dns['ms']}ms)")
    else:
        lines.append(f"  DNS resolution:    FAILED ({dns.get('error', 'unknown')})")

    # 2. Direct TCP latency to target
    async def _run_tcp():
        return await tcp_connect_latency(target_host, target_port)

    tcp_result = asyncio.run(_run_tcp())
    if tcp_result["ok"]:
        lines.append(f"  Target TCP connect: {tcp_result['avg_ms']}ms avg ({tcp_result['min_ms']}-{tcp_result['max_ms']}ms, {tcp_result['samples']} samples)")
    else:
        lines.append(f"  Target TCP connect: FAILED (unreachable)")

    # 3. Proxy port TCP latency
    async def _run_proxy():
        return await tcp_connect_latency("127.0.0.1", listen_port)

    proxy_tcp = asyncio.run(_run_proxy())
    if proxy_tcp["ok"]:
        lines.append(f"  Proxy TCP connect:  {proxy_tcp['avg_ms']}ms avg ({proxy_tcp['min_ms']}-{proxy_tcp['max_ms']}ms, {proxy_tcp['samples']} samples)")
    else:
        lines.append(f"  Proxy TCP connect:  FAILED (proxy not listening?)")

    # 4. RTT through proxy
    async def _run_rtt():
        return await proxy_rtt("127.0.0.1", listen_port)

    rtt = asyncio.run(_run_rtt())
    if rtt["ok"]:
        lines.append(f"  RTT via proxy:      {rtt['avg_ms']}ms avg ({rtt['min_ms']}-{rtt['max_ms']}ms, {rtt['samples']} samples)")
    else:
        lines.append(f"  RTT via proxy:      FAILED")

    # 5. Tailscale check
    ts = check_tailscale(target_host)
    if ts["installed"]:
        if ts["connected"]:
            if ts["direct"]:
                lines.append(f"  Tailscale:          direct connection")
                if ts["path"]:
                    lines.append(f"                      {ts['path']}")
            elif ts["relay"]:
                lines.append(f"  Tailscale:          relayed — {ts['relay']}")
                lines.append(f"  ⚠  High latency likely due to DERP relay")
                lines.append(f"     Suggestion: Ensure UDP 41641 is open on both nodes")
                lines.append(f"     for Tailscale to establish a direct connection.")
        else:
            lines.append(f"  Tailscale:          node not found in status")
    else:
        lines.append(f"  Tailscale:          not installed or not available")

    # 6. Summary
    lines.append("")
    if tcp_result["ok"] and tcp_result["avg_ms"] and tcp_result["avg_ms"] > 100:
        lines.append(f"  ⚠  High latency detected: {tcp_result['avg_ms']}ms to target")
        if ts["relay"]:
            lines.append(f"     Root cause: Traffic routed via Tailscale DERP relay")
            lines.append(f"     Fix: Open UDP 41641 in firewall for direct connection")
    elif tcp_result["ok"]:
        lines.append(f"  ✓  Latency looks good ({tcp_result['avg_ms']}ms to target)")

    return "\n".join(lines)
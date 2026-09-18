"""Shared helpers for start_desktop / stop_desktop / desktop_status.

Addressing is deliberately defensive. mDNS resolution of `desktop.local` from this Pi proved
**intermittent** — the same `getent hosts` call succeeded and then failed a minute later — and an
intermittently-resolvable name is worse than no name at all for a remote power switch: it fails
exactly when you are away and cannot investigate. So resolution is a fallback chain that also
learns, and nothing here depends on mDNS working:

    1. the hostname (mDNS), with a short timeout
    2. the last address that actually worked, cached on disk
    3. the compiled-in default

Any successful contact refreshes the cache, so a DHCP lease change self-heals as soon as mDNS
works once — and until then the cached address keeps working.

Waking does not need an address at all: the magic packet is addressed by MAC to the broadcast
address, so a machine whose lease has changed still wakes.
"""

import socket
import subprocess
from pathlib import Path

# The bench host's wired NIC. The MAC is what wakes it and never changes.
MAC = "40:b0:76:a4:0a:2a"
HOSTNAME = "desktop.local"
DEFAULT_IP = "192.168.178.22"
BROADCAST = "192.168.178.255"
SSH_PORT = 22
SSH_USER = "gregor"

CACHE = Path.home() / ".cache" / "desktop_ip"


def cached_ip():
    try:
        value = CACHE.read_text().strip()
        return value or None
    except OSError:
        return None


def remember(ip):
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(ip + "\n")
    except OSError:
        pass   # a read-only home should not break a power switch


def candidates(explicit=None):
    """Addresses to try, best first, without duplicates."""
    order, seen = [], set()
    for value in (explicit, HOSTNAME, cached_ip(), DEFAULT_IP):
        if value and value not in seen:
            seen.add(value)
            order.append(value)
    return order


def port_open(address, port=SSH_PORT, timeout=2.0):
    """True if something accepts TCP on that port.

    Reachability is TCP/22, never ping: the bench host drops ICMP, so a ping-based check reports
    failure on a machine that is up and perfectly usable.
    """
    try:
        with socket.create_connection((address, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def resolve(explicit=None, timeout=2.0):
    """First candidate whose SSH port answers, or None if the host is down/unreachable."""
    for address in candidates(explicit):
        if port_open(address, timeout=timeout):
            # Store the numeric form so the cache stays useful when mDNS is the thing that broke.
            try:
                remember(socket.gethostbyname(address))
            except (socket.gaierror, OSError):
                remember(address)
            return address
    return None


def ssh(address, command, timeout=15):
    """Run a command over SSH.

    Returns (ok, output). `ok` is False both when the command failed and when SSH could not run at
    all — callers that care about the difference must check reachability separately. Every caller
    here does, because reporting "no campaign running" when the truth is "could not ask" is how a
    ten-hour benchmark gets powered off by mistake.
    """
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
             f"{SSH_USER}@{address}", command],
            capture_output=True, text=True, timeout=timeout)
        return result.returncode == 0, result.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return False, ""


def campaign_state(address):
    """('running'|'idle'|'unknown', detail).

    'unknown' is a distinct outcome on purpose: SSH failing must never be reported as 'idle'.
    """
    ok, out = ssh(address, "pgrep -af '[r]un_campaign.py' | head -1")
    if not ok and not out:
        # Distinguish "asked, nothing there" from "could not ask" by probing with a command that
        # must succeed on any reachable host.
        alive, _ = ssh(address, "true")
        if not alive:
            return "unknown", "ssh did not answer"
        return "idle", None
    if not out:
        return "idle", None

    detail = None
    ok, status = ssh(address, "cat $(ls -t ~/ConnectionLibBenchmark/results/*/status.json "
                               "2>/dev/null | head -1) 2>/dev/null")
    if ok and status:
        try:
            import json
            data = json.loads(status)
            detail = (f"{data.get('points_done')}/{data.get('points_total')} points, "
                       f"current {data.get('current')}")
        except (ValueError, TypeError):
            pass
    return "running", detail or out

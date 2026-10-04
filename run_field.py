#!/usr/bin/env python3
"""One field site, from a laptop with the node and the PPK2 on it.

    ./run_field.py --site "desk, 0 m" --techs lora --budget-hours 0.3   # the short first run
    ./run_field.py --site "1 km, ridge"                                 # all three, standard
    ./run_field.py --resume results/campaign-20261004-1200              # --site is remembered
    ./run_field.py --site "desk, 0 m" --dry-run --techs lora            # the plan, no hardware

A thin front end to `run_campaign.py --field`, which does all the work: it resolves the node by
USB serial, raises the PPK2 rail before looking for the board, flashes `field_node`, paces the
duty cycle and prints its own resume and fetch hints. Everything here is **preflight** - the
checks `run_campaign.py` cannot make for itself, each of which otherwise fails late, in the field,
or not at all:

* **`ppk2-api` missing from PlatformIO's venv.** On the bench that only costs the energy columns;
  in source mode the PPK2 *is* the node's supply, so it costs the whole run. The ThinkPad's venv
  had PlatformIO and pyserial but not this, and `ensure_pyserial()` re-execs into exactly that
  interpreter - so the check has to be made there, not here.
* **Something else holding the PPK2.** The nRF Connect Power Profiler keeps the port open
  exclusively, and the failure surfaces as "device reports readiness to read but returned no data".
* **No `--gateway-mac`.** ESP-NOW needs it and `run_campaign.py` refuses without it - a long way
  from the desk where the gateway was flashed. It is cached on first use instead.
* **An empty airtime ledger on a host that has never run.** The ledger persists LoRa airtime
  across runs (findings.md §7), but a fresh laptop's is empty by construction, and that says
  nothing about the *gateway's* ledger, which spans its uptime. Power-cycling it is the only way
  to clear that one.

See docs/field-runbook.md for the wiring and the order of operations.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench.pacing import AirtimeBudget
from bench.ports import ensure_pyserial

REPO_ROOT = Path(__file__).resolve().parent
AIRTIME_LEDGER = REPO_ROOT / "results" / "lora_airtime_ledger.txt"

# Written on the first run that is told the MAC, so later sites need only --site. Beside the
# results rather than in git: it describes this pair of boards, not the benchmark.
MAC_CACHE = REPO_ROOT / "results" / "field_gateway_mac.txt"

RED, YELLOW, GREEN, DIM, RESET = "\033[31m", "\033[33m", "\033[32m", "\033[2m", "\033[0m"


def ok(msg):
    print(f"  {GREEN}ok{RESET}    {msg}")


def warn(msg):
    print(f"  {YELLOW}warn{RESET}  {msg}")


def fail(msg):
    sys.exit(f"  {RED}fail{RESET}  {msg}")


def check_ppk2(pio):
    """A PPK2 that will actually stream, under the interpreter the campaign will use.

    `ensure_pyserial` has already re-exec'd us into PlatformIO's venv by the time this runs, so an
    ImportError here is the real answer for the campaign too, not for some other Python.
    """
    try:
        import ppk2_api   # noqa: F401
    except ImportError:
        venv_pip = Path(sys.executable).parent / "pip"
        fail(f"ppk2-api is not installed for {sys.executable}.\n"
             f"        In source mode the PPK2 is the node's only supply, so the campaign cannot\n"
             f"        start without it. Install it there:  {venv_pip} install ppk2-api")

    from bench.ppk2_port import find_port, port_holder

    port = find_port(log=lambda _msg: None)
    if not port:
        fail("no PPK2 on /dev/serial/by-id. It powers the node — without it the node has no\n"
             "        rail and no serial port either. Check the USB cable to the PPK2 itself.")
    holder = port_holder(port)
    if holder:
        fail(f"{holder} is holding {port}.\n"
             f"        A PPK2 port is exclusive: the nRF Connect Power Profiler and a left-over\n"
             f"        tools/bench_board.py are the two usual culprits. Close it and retry.")
    ok(f"PPK2 on {port}, nothing holding it")


def check_boards():
    """The gateway must not be on this host during a run — it belongs in the field.

    A warning rather than an error: the gateway is legitimately plugged in afterwards, for
    `tools/field_gateway.py fetch`, and this script is also how a run gets restarted.
    """
    from bench.board_session import KNOWN_BOARDS, board_ports, describe_board

    gateway_serial = next(s for s, role in KNOWN_BOARDS.items() if role == "gateway")
    attached = board_ports()
    if any(gateway_serial in p.name for p in attached):
        warn(f"the gateway board ({gateway_serial}) is attached to this host. In field mode it has\n"
             f"        no host at all — it should be on power only, wherever it is deployed.")
    # The node has no serial port until the PPK2 raises its rail, so its absence proves nothing
    # and run_campaign.py waits for it properly. Only report what is here.
    print(f"  {DIM}      boards attached: "
          f"{', '.join(describe_board(p) for p in attached) or 'none'}{RESET}")


def check_airtime(techs):
    if "lora" not in techs:
        return
    budget = AirtimeBudget(path=AIRTIME_LEDGER)
    carried = budget.used()
    if carried > 0:
        ok(f"airtime ledger: {carried:.0f}s of {budget.limit_s:.0f}s still inside the hour "
           f"— the run will pace around it")
    else:
        warn("the airtime ledger is empty, so it cannot vouch for the gateway's.\n"
             "        The gateway keeps its own ledger over its whole uptime and will silently\n"
             "        drop replies it cannot afford (findings.md §7). Power-cycle it before a\n"
             "        first run from this host, or if LoRa ran on these boards within the hour.")


def resolve_mac(args):
    """The gateway's MAC: from the flag, else from the cache the first run wrote."""
    if args.gateway_mac:
        MAC_CACHE.parent.mkdir(parents=True, exist_ok=True)
        MAC_CACHE.write_text(args.gateway_mac + "\n")
        ok(f"gateway MAC {args.gateway_mac} (cached in {MAC_CACHE.relative_to(REPO_ROOT)})")
        return args.gateway_mac
    if MAC_CACHE.exists():
        mac = MAC_CACHE.read_text().strip()
        ok(f"gateway MAC {mac}, from {MAC_CACHE.relative_to(REPO_ROOT)}")
        return mac
    if "espnow" in args.techs_effective:
        fail("ESP-NOW needs the field gateway's MAC and none is cached.\n"
             "        Read it with  tools/field_gateway.py mac  while the gateway is on USB,\n"
             "        then pass --gateway-mac once; later sites reuse it.")
    warn("no gateway MAC known. Fine for LoRa and Zigbee; ESP-NOW points would not reach it.")
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site", help="where the node is. Recorded in meta.json and the only "
                                       "record of it — required unless --resume")
    parser.add_argument("--gateway-mac", help="the field gateway's MAC, from "
                                              "tools/field_gateway.py mac. Cached after the first use")
    parser.add_argument("--techs", nargs="+", help="default: all three. Leave it off when "
                                                    "resuming — the campaign's own plan wins")
    parser.add_argument("--resume", help="continue an existing campaign directory")
    parser.add_argument("--pio", default="pio")
    args, passthrough = parser.parse_known_args()

    # Before any output: this re-execs under PlatformIO's interpreter, and on a line-buffered
    # terminal anything already printed would then appear twice.
    ensure_pyserial(args.pio)

    if not args.site and not args.resume:
        parser.error("--site is required: it is the only record of where the node was")

    args.techs_effective = args.techs or ["lora", "espnow", "zigbee"]

    print("preflight")
    # A dry run prints the plan and touches no hardware, so it must also work with nothing
    # plugged in - including from a desk a long way from the PPK2.
    if "--dry-run" in passthrough or "--no-power" in passthrough:
        print(f"  {DIM}      skipping the PPK2 and board checks{RESET}")
    else:
        check_ppk2(args.pio)
        check_boards()
    check_airtime(args.techs_effective)
    mac = resolve_mac(args)

    command = [sys.executable, str(REPO_ROOT / "run_campaign.py"), "--field", "--pio", args.pio]
    # Only when asked for: run_campaign.py adopts a resumed campaign's recorded technologies, and
    # refuses a plan that would move a recorded point (findings.md §7). Passing a default here
    # would turn every resume into exactly that refusal.
    if args.techs:
        command += ["--techs", *args.techs]
    if args.site:
        command += ["--site", args.site]
    if args.resume:
        command += ["--resume", args.resume]
    if mac:
        command += ["--gateway-mac", mac]
    command += passthrough

    print(f"\n{DIM}$ {subprocess.list2cmdline(command)}{RESET}\n")
    # execv does not flush, so into a pipe - `| tee field.log` is the obvious way to run this -
    # the whole preflight would be discarded with the buffer. It was, the first time.
    sys.stdout.flush()
    # exec rather than spawn: run_campaign.py owns the PPK2 session for the whole campaign, and
    # it prints its own resume and fetch hints on the way out. A wrapper would add nothing but a
    # second signal handler between Ctrl-C and the rail.
    os.execv(sys.executable, command)


if __name__ == "__main__":
    sys.exit(main())

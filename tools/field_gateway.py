#!/usr/bin/env python3
"""Deploy and collect the field gateway (docs/field-campaign.md).

    tools/field_gateway.py flash                         # before deployment: flash, print the MAC
    tools/field_gateway.py mac                           # the MAC again, for --gateway-mac
    tools/field_gateway.py status                        # is it alive, how big is its log
    tools/field_gateway.py fetch --results results/X     # after collection: arrivals -> node_rx.csv
    tools/field_gateway.py clear --yes                   # empty the log once every campaign is fetched

The gateway must be the board with the Zigbee coordinator (docs/hardware.md). The tool finds it by
its USB serial number and refuses to touch any other board, so the node can stay plugged in.

`fetch` keeps the raw log beside the results as gateway_log.txt, then rewrites node_rx.csv and
writes gateway_leases.csv. It matches records by the campaign id in meta.json, so one log can hold
several campaigns, and fetching twice gives the same files.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench.board_session import BY_ID, KNOWN_BOARDS, board_ports, describe_board
from bench.ports import ensure_pyserial, flash, read_mac_for_port

COORDINATOR_SERIAL = next(serial for serial, role in KNOWN_BOARDS.items() if role == "coordinator")


def resolve_port(args):
    """The coordinator board's by-id path, and never any other board's.

    Picking "the one attached board" is not safe: the node shares the desk, and with its VBUS cut
    it can enumerate on its own - it once did, and was nearly flashed as the gateway.
    """
    if args.port:
        port = Path(args.port)
        resolved = port.resolve()
        by_id = [p for p in board_ports() if p.resolve() == resolved] or [port]
        if COORDINATOR_SERIAL not in by_id[0].name and not args.force:
            sys.exit(f"{args.port} is {describe_board(by_id[0])}, not the coordinator "
                     f"({COORDINATOR_SERIAL}); pass --force if the boards were deliberately swapped")
        return args.port
    for path in board_ports():
        if COORDINATOR_SERIAL in path.name:
            return str(path)
    present = ", ".join(describe_board(p) for p in board_ports()) or "none"
    sys.exit(f"the coordinator board ({COORDINATOR_SERIAL}) is not attached; boards on "
             f"{BY_ID}: {present}")


def cmd_flash(args, port):
    flash(args.pio, "field_gateway", port)
    cmd_mac(args, port)


def cmd_mac(args, port):
    mac = read_mac_for_port(port, args.pio)
    print(f"field gateway MAC: {mac}\n  run_campaign.py --field --gateway-mac {mac}")


def cmd_status(args, port):
    from bench.device import Device

    with Device(port, name="field-gw") as board:
        print(f"says: {board.ping()}")
        info = board.info()
    print(f"log: {info.get('fieldLogBytes', '?')} bytes, points run: {info.get('pointsRun', '?')}")
    if info.get("role") != "field-gateway":
        print(f"warning: role is {info.get('role')!r}, not the field gateway firmware")


def cmd_fetch(args, port):
    from bench.device import Device
    from bench.field_log import rebuild_node_rx

    results = Path(args.results)
    if not (results / "meta.json").exists():
        sys.exit(f"{results} has no meta.json")
    with Device(port, name="field-gw") as board:
        lines = board.read_log()
    (results / "gateway_log.txt").write_text("\n".join(lines) + "\n")

    summary = rebuild_node_rx(results, lines)
    print(f"{len(lines)} log records, {summary['leases']} leases from this campaign")
    print(f"  {summary['points_with_lease']} points with a lease record, "
          f"{summary['arrivals']} node->gateway arrivals -> node_rx.csv")
    print(f"  lease endings: {summary['lease_endings']}")
    if summary["points_without_lease"]:
        missing = summary["points_without_lease"]
        print(f"  {len(missing)} measured points have no lease record: {missing[:20]}"
              f"{' ...' if len(missing) > 20 else ''}")
    if summary["reboots"]:
        print(f"  the gateway rebooted {len(summary['reboots'])} time(s) during the campaign: "
              f"{summary['reboots']}")


def cmd_clear(args, port):
    from bench.device import Device

    if not args.yes:
        sys.exit("clear deletes every campaign's records from the gateway; "
                 "fetch them first, then pass --yes")
    with Device(port, name="field-gw") as board:
        board.clear_log()
    print("log cleared")


COMMANDS = {"flash": cmd_flash, "mac": cmd_mac, "status": cmd_status, "fetch": cmd_fetch,
            "clear": cmd_clear}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--port")
    parser.add_argument("--force", action="store_true",
                        help="accept a --port that is not the coordinator board")
    parser.add_argument("--pio", default="pio")
    parser.add_argument("--results", help="fetch: the field campaign's results directory")
    parser.add_argument("--yes", action="store_true", help="clear: really delete the log")
    args = parser.parse_args()
    if args.command == "fetch" and not args.results:
        parser.error("fetch needs --results")

    ensure_pyserial(args.pio)
    COMMANDS[args.command](args, resolve_port(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())

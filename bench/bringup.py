"""Bringing the bench up: the power rail first, then the two boards.

Shared by run_campaign.py and run_benchmark.py, which had the same sequence written out twice.

The order is not incidental. In source mode the PPK2 *is* the node's supply, and the node's
USB-serial chip runs off the board rail because its cable has VBUS cut - so until the rail is up
the node has no serial port to detect, flash, or open. DUT power also drops the moment the PPK2
session closes, which is why the monitor returned here has to outlive the whole run rather than
being opened per point.
"""

import sys

from .ports import detect_ports, wait_for_port
from .power import PowerMonitor


def start_power(args, raw_dir):
    """Start the PPK2, or exit if source mode was asked for and it would not come up.

    Returns None when energy measurement is off or unavailable - the campaign then runs exactly as
    before, without energy columns. In source mode that fallback is not available: there would be
    no power at all.
    """
    if args.no_power:
        return None
    power = PowerMonitor.create(port=args.power_port, voltage_mv=args.power_voltage_mv,
                                 mode=args.power_mode, raw_dir=raw_dir)
    if power is None and args.power_mode == "source":
        sys.exit("source mode: the PPK2 is the node's only supply, but it would not start. "
                 "Fix it, or pass --no-power if the node is powered some other way.")
    return power


def resolve_ports(args):
    """(gateway_port, node_port), detecting them if they were not given, and waiting for both.

    The wait matters after start_power(): the boards enumerate only once the rail is up, so a
    scan run immediately would find nothing.
    """
    gw_port, node_port = args.gateway_port, args.node_port
    if not gw_port or not node_port:
        detected = detect_ports(args.pio, expect=2, timeout_s=20.0)
        if len(detected) != 2:
            sys.exit(f"expected exactly 2 serial devices, found {len(detected)}: {detected}")
        gw_port = gw_port or detected[0]
        node_port = node_port or detected[1]
    for port in (gw_port, node_port):
        if not wait_for_port(port):
            sys.exit(f"{port} never appeared — is the PPK2 powering that board?")
    return gw_port, node_port

#!/usr/bin/env python3
"""Pre-flight for the energy campaign: board strip, IDLE command, and the PPK2 if attached.

    ./tools/energy_check.py --node-port ... --gateway-port ...

Order matters. Each check is a prerequisite for the next one being meaningful, and the standing
currents at the end are the sanity check that decides whether anything measured afterwards can be
believed at all.
"""

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench import technologies
from bench.device import Device, DeviceError, detect_ports, ensure_pyserial, read_mac_for_port
from bench.power import PowerMonitor
from bench.records import parse_records

PASS, FAIL, INFO = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m", "\033[2mINFO\033[0m"


def check(name, condition, detail=""):
    print(f"  {PASS if condition else FAIL}  {name}")
    for line in str(detail).split("\n"):
        if line:
            print(f"         {line}")
    return bool(condition)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gateway-port")
    parser.add_argument("--node-port")
    parser.add_argument("--zigbee-peer", default="0013A20042035B39")
    parser.add_argument("--pio", default="pio")
    parser.add_argument("--power-port")
    parser.add_argument("--idle-seconds", type=float, default=6.0,
                         help="shorter than a campaign's hold; enough to compare standing currents")
    args = parser.parse_args()

    ensure_pyserial(args.pio)
    gw_port, node_port = args.gateway_port, args.node_port
    if not gw_port or not node_port:
        detected = detect_ports(args.pio)
        if len(detected) != 2:
            sys.exit(f"expected 2 serial devices, found {detected}")
        gw_port, node_port = detected

    node_mac = read_mac_for_port(node_port, args.pio)
    gateway_mac = read_mac_for_port(gw_port, args.pio)
    ok = True

    with Device(gw_port, name="gw") as gateway, Device(node_port, name="node") as node:
        print("\n1. Board strip (the node is the instrumented board)")
        info = node.info()
        rails = {k: v for k, v in info.items() if k.startswith("rail")}
        aldo3 = info.get("railALDO3", "?")
        ok &= check("GPS rail (ALDO3) disabled", aldo3.startswith("0:"),
                    f"ALDO3={aldo3} — a live GPS is tens of mA sitting under every measurement")
        ok &= check("LoRa rail (ALDO2) enabled", info.get("railALDO2", "").startswith("1:"),
                    f"ALDO2={info.get('railALDO2')}")
        ok &= check("display not compiled in", info.get("display") == "off",
                    f"display={info.get('display')}")
        print(f"  {INFO}  all rails: {rails}")

        print("\n2. IDLE command")
        try:
            node.configure({"tech": "none", "payload": 32})
            records = node.idle(1500)
            _, _, summary, _, _ = parse_records(records)
            ok &= check("IDLE returns a PPI record", summary is not None and summary.get("idle"),
                        f"held {summary['elapsed_ms']} ms, {summary['polls']} polls"
                        if summary else "no record")
        except DeviceError as exc:
            ok &= check("IDLE returns a PPI record", False, str(exc))

        print("\n3. PPK2")
        power = PowerMonitor.create(port=args.power_port, raw_dir=None)
        if power is None:
            print(f"  {INFO}  no PPK2 attached — wire it and re-run to get standing currents")
            print("\nboard checks passed" if ok else "\nSOME CHECKS FAILED")
            return 0 if ok else 1
        check("PPK2 streaming", True, f"source meter at {power.voltage_mv} mV")

        print("\n4. Standing currents — the sanity check that decides everything after it")
        hold_ms = int(args.idle_seconds * 1000)
        measured = {}
        for tech, extra in (("none", {}),
                            ("lora", {}),
                            ("zigbee", {"peerxbee": args.zigbee_peer}),
                            ("espnow", {"peermac": gateway_mac})):
            try:
                node.configure({"tech": tech, "payload": 32, **extra})
                window = power.open(f"{tech}/idle/check", 0)
                node.idle(hold_ms)
                result = power.close(window)
                measured[tech] = result["mean_ua"] / 1000.0 if result else None
                print(f"  {INFO}  {tech:<7} {measured[tech]:.2f} mA"
                      if measured[tech] is not None else f"  {FAIL}  {tech}: no samples")
            except DeviceError as exc:
                measured[tech] = None
                print(f"  {FAIL}  {tech}: {exc}")

        power.stop()

        floor = measured.get("none")
        ok &= check("board floor is plausible", floor is not None and 1.0 < floor < 500.0,
                    f"{floor} mA — near zero means the PPK2 is not actually powering the board, "
                    f"and a very large value means something else is still on" if floor
                    else "not measured")
        # WiFi has to be up for ESP-NOW to receive at all, and that is expensive. If it does not
        # stand out clearly, the rail being measured is not the one carrying the radio.
        if measured.get("espnow") and measured.get("lora"):
            ratio = measured["espnow"] / measured["lora"]
            ok &= check("ESP-NOW idle clearly above LoRa idle", ratio > 1.5,
                        f"ESP-NOW {measured['espnow']:.1f} mA vs LoRa {measured['lora']:.1f} mA "
                        f"({ratio:.1f}x). Similar values would mean the measurement is dominated "
                        f"by something common to both, not by the radios.")

    print("\nall checks passed" if ok else "\nSOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

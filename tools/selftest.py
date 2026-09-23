#!/usr/bin/env python3
"""Hardware self-test: the claims the benchmark's design rests on, checked against real boards.

    ./tools/selftest.py --gateway-port ... --node-port ...

Each check corresponds to something the rest of the suite assumes and would otherwise fail
confusingly later:

1. Both boards answer the command protocol at all.
2. The conformance guard rejects configurations outside the EU envelope (docs/regulations.md).
3. Technologies can be switched at run time - the claim that makes a one-flash campaign possible.
4. WiFi really is off for non-ESP-NOW runs, so practice P5's external-load check means something.
5. The null measurement produces an instrumentation-overhead figure (practice P4).
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench.device import Device
from bench.ports import DeviceError, detect_ports, ensure_pyserial, read_mac_for_port
from bench.records import parse_records

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"


def check(name, condition, detail=""):
    print(f"  {PASS if condition else FAIL}  {name}")
    if detail:
        for line in str(detail).split("\n"):
            print(f"         {line}")
    return bool(condition)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gateway-port")
    parser.add_argument("--node-port")
    parser.add_argument("--zigbee-peer", default="0013A20042035B39")
    parser.add_argument("--pio", default="pio")
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
        print("\n1. Command protocol")
        gw_role = gateway.ping()
        node_role = node.ping()
        ok &= check("both boards answer PING", gw_role == "gateway" and node_role == "node",
                    f"gateway={gw_role}, node={node_role}")

        info = gateway.info()
        ok &= check("INFO reports a release build with a microsecond timer",
                    info.get("optimized") == "1" and info.get("timer") == "micros",
                    f"optimized={info.get('optimized')}, coreDebugLevel="
                    f"{info.get('coreDebugLevel')}, timer={info.get('timer')}")

        print("\n2. Conformance guard (docs/regulations.md)")
        rejections = [
            ("bandwidth 500 kHz at a 433.2 MHz centre leaves the band",
             {"tech": "lora", "bw": 500, "n": 5}),
            ("17 dBm exceeds the 10 mW e.r.p. ceiling",
             {"tech": "lora", "pwr": 17, "n": 5}),
            ("433.0 MHz centre puts a 125 kHz signal below the band edge",
             {"tech": "lora", "freq": 433.0, "n": 5}),
            ("90 B exceeds Zigbee's 84 B payload limit",
             {"tech": "zigbee", "payload": 90, "n": 5}),
        ]
        for description, params in rejections:
            try:
                gateway.configure(params)
                ok &= check(f"rejects: {description}", False, "it was ACCEPTED")
            except DeviceError as exc:
                ok &= check(f"rejects: {description}", True, str(exc).split(":", 1)[-1].strip())

        legal = {"tech": "lora", "sf": 9, "bw": 125, "pwr": 7, "freq": 433.2, "n": 5}
        try:
            gateway.configure(legal)
            ok &= check("accepts the legal baseline", True)
        except DeviceError as exc:
            ok &= check("accepts the legal baseline", False, str(exc))

        print("\n3. Runtime technology switching (no reflash)")
        headers = {}
        for tech, extra in (("espnow", {"peermac": node_mac}),
                            ("zigbee", {"peerxbee": args.zigbee_peer}),
                            ("lora", {})):
            params = {"tech": tech, "payload": 16, "n": 5, "spacing": 50, **extra}
            try:
                headers[tech] = gateway.configure(params)
                ok &= check(f"configured {tech}", headers[tech].get("technology") == tech,
                            f"technologyName={headers[tech].get('technologyName')}")
            except DeviceError as exc:
                ok &= check(f"configured {tech}", False, str(exc))

        print("\n4. External load: WiFi state (practice P5)")
        for tech, expected in (("lora", "off"), ("zigbee", "off"), ("espnow", "on")):
            actual = headers.get(tech, {}).get("wifi")
            ok &= check(f"{tech}: wifi={expected}", actual == expected, f"reported {actual}")

        print("\n5. Null measurement (practice P4)")
        try:
            gateway.configure({"tech": "none", "n": 50, "payload": 32})
            records = gateway.run(timeout_s=30)
            _, exchanges, summary, _, _ = parse_records(records)
            overheads = [e["rtt_us"] for e in exchanges]
            median = sorted(overheads)[len(overheads) // 2] if overheads else None
            ok &= check("harness overhead measured", median is not None,
                        f"median {median} µs over {len(overheads)} iterations — the floor under "
                        f"every latency figure the suite reports")
        except DeviceError as exc:
            ok &= check("harness overhead measured", False, str(exc))

        print("\n6. End-to-end exchange, each technology")
        for tech, extra in (("espnow", {"peermac": node_mac}),
                            ("zigbee", {"peerxbee": args.zigbee_peer}),
                            ("lora", {})):
            try:
                node_params = {"tech": tech, "payload": 16, "spacing": 50, "replywait": 4000,
                               **({"peermac": gateway_mac} if tech == "espnow" else extra)}
                node.configure(node_params)
                gateway.configure({"tech": tech, "payload": 16, "n": 5, "spacing": 50,
                                    "replywait": 4000, **extra})
                records = gateway.run(timeout_s=90)
                _, exchanges, summary, _, _ = parse_records(records)
                good = [e for e in exchanges if e["ok"]]
                rtt = sorted(e["rtt_us"] for e in good)
                median = rtt[len(rtt) // 2] / 1000.0 if rtt else 0.0
                ok &= check(f"{tech}: 5 exchanges completed",
                            summary is not None and summary["successes"] >= 5,
                            f"median rtt {median:.2f} ms, "
                            f"{summary['successes'] if summary else 0}/5")
            except DeviceError as exc:
                ok &= check(f"{tech}: 5 exchanges completed", False, str(exc))

    print("\n" + ("all checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

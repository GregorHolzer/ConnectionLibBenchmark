#!/usr/bin/env python3
"""Run one technology's benchmark, once or a few times.

    ./run_benchmark.py --tech lora
    ./run_benchmark.py --tech espnow --repetitions 3
    ./run_benchmark.py --tech zigbee --quick        # 10 exchanges, first 4 points - a smoke test

This is the short-loop tool for developing or spot-checking one radio. For anything that will be
analysed for reliability, use run_campaign.py instead: a single repetition cannot distinguish
random from systematic error, which practice P2 makes the whole basis of trusting a number.
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench import technologies
from bench.campaign import CampaignRunner
from bench.schedule import ScheduledRun
from bench.bringup import resolve_ports, start_power
from bench.device import Device
from bench.ports import ensure_pyserial, flash, read_mac_for_port
from bench.power import DEFAULT_VOLTAGE_MV
from bench.results import ResultWriter, write_meta
from bench.util import DEFAULT_ZIGBEE_PEER, format_duration, git_sha

REPO_ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tech", required=True, choices=technologies.ALL)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--quick", action="store_true",
                         help="4 points, 10 exchanges each - checks the path, not the radio")
    parser.add_argument("--gateway-port")
    parser.add_argument("--node-port")
    parser.add_argument("--zigbee-peer", default=DEFAULT_ZIGBEE_PEER)
    parser.add_argument("--pio", default="pio")
    parser.add_argument("--out")
    parser.add_argument("--flash", action="store_true", help="reflash before running")
    parser.add_argument("--power-port",
                         help="PPK2 serial port (auto-detected if a PPK2 is attached)")
    parser.add_argument("--power-voltage-mv", type=int, default=DEFAULT_VOLTAGE_MV,
                         help="source-meter voltage; 5000 is the node's 5V header pin")
    parser.add_argument("--power-mode", choices=["source", "ampere"], default="source",
                         help="source: PPK2 powers the node from its 5V header pin. ampere: "
                              "PPK2 sits in series with an externally powered rail")
    parser.add_argument("--no-power", action="store_true",
                         help="skip energy measurement even if a PPK2 is attached")
    parser.add_argument("--rssi", action="store_true",
                        help="capture per-packet RSSI/SNR. Free on LoRa, but costs a promiscuous "
                             "sniffer on ESP-NOW and a DB AT query per receive on Zigbee, both of "
                             "which shift the energy and latency figures - so a campaign run with "
                             "this is a signal-quality campaign, not a timing one, and meta.json "
                             "records which it was.")
    args = parser.parse_args()

    ensure_pyserial(args.pio)
    module = technologies.get(args.tech)
    points = module.points()
    if args.quick:
        points = points[:4]

    out_dir = Path(args.out) if args.out else (
        REPO_ROOT / "results" / f"{args.tech}-{datetime.now():%Y%m%d-%H%M%S}")
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # Rail first, then the boards: see bench/bringup.py for why that order is forced. DUT power
    # drops the moment the PPK2 session closes - verified by SIGKILLing a sole holder and watching
    # the board's by-id entry vanish - so this one monitor has to span the entire run.
    power = start_power(args, out_dir / "power_traces")
    gw_port, node_port = resolve_ports(args)

    if args.flash:
        flash(args.pio, "bench_node", node_port)
        flash(args.pio, "bench_gateway", gw_port)

    node_mac = read_mac_for_port(node_port, args.pio)
    gateway_mac = read_mac_for_port(gw_port, args.pio)
    gateway_peer = {"mac": node_mac, "xbee64": args.zigbee_peer}
    node_peer = {"mac": gateway_mac, "xbee64": args.zigbee_peer}

    # Repetition-major even here, so a multi-repetition run still obeys practice P9.
    schedule = [ScheduledRun(i, rotation, point)
                for i, (rotation, point) in enumerate(
                    (r, p) for r in range(args.repetitions) for p in points)]

    writer = ResultWriter(out_dir)
    started = time.time()

    with Device(gw_port, name="gw") as gateway, Device(node_port, name="node") as node:
        print(f"gateway {gateway.ping()}   node {node.ping()}")

        # `power` was started above, before the boards were touched. The metadata below still
        # records whether energy was actually being captured rather than whether it was asked for.
        write_meta(out_dir, {
            "started_at": started,
            "started_at_iso": datetime.now().isoformat(timespec="seconds"),
            "techs": [args.tech],
            "single_technology_run": True,
            "quick": args.quick,
            "repetitions": args.repetitions,
            "scheduled_points": len(schedule),
            "device_info": {"gateway": gateway.info(), "node": node.info()},
            "peer": gateway_peer,
            "power": ({"mode": args.power_mode, "voltage_mv": args.power_voltage_mv,
                        "measured_board": "node"} if power else None),
            "benchmark_git_sha": git_sha(REPO_ROOT),
            "connectionlib_git_sha": git_sha(REPO_ROOT / "ConnectionLib"),
            "rssi_capture": bool(args.rssi),
            "configuration_space": {
                args.tech: [{"factor": p.factor, "level": p.level, "params": p.params}
                             for p in module.points()]
            },
        })

        runner = CampaignRunner(gateway, node, gateway_peer, node_peer, out_dir, writer,
                                 out_dir / "status.json", power=power, capture_rssi=args.rssi)

        override = 10 if args.quick else None
        for run in schedule:
            print(f"[{run.index + 1}/{len(schedule)}] rot={run.rotation} {run.point.key}",
                  flush=True)
            try:
                ok, exchanges, summary = runner.run_one(run, exchanges_override=override)
            except Exception as exc:   # noqa: BLE001
                print(f"    FAILED: {exc}")
                runner.failures.append({"index": run.index, "point": run.point.key,
                                         "error": str(exc)})
                runner.completed += 1
                continue

            if ok and exchanges:
                rtts = sorted(e["rtt_us"] for e in exchanges if e["ok"])
                median = rtts[len(rtts) // 2] / 1000.0 if rtts else 0.0
                expected = override or module.exchanges(run.point)
                print(f"    ok  {summary['successes']}/{expected}  "
                      f"median rtt {median:.2f} ms  retries {summary['retries']}")
            else:
                got = summary["successes"] if summary else 0
                print(f"    INCOMPLETE {got}/{override or module.exchanges(run.point)}")
            runner.completed += 1
            runner._write_status(schedule)

        if power:
            power.stop()

    writer.close()
    print(f"\nfinished in {format_duration(time.time() - started)} -> {out_dir}")
    if runner.failures:
        print(f"{len(runner.failures)} point(s) failed")
    return 1 if runner.failures else 0


if __name__ == "__main__":
    sys.exit(main())

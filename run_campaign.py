#!/usr/bin/env python3
"""Run a full benchmark campaign across all three technologies.

    ./run_campaign.py --preset quick      # ~30 min, enough to shake the pipeline out
    ./run_campaign.py --preset standard   # ~3 h
    ./run_campaign.py --preset full       # ~10 h, the one that produces reliability data
    ./run_campaign.py --resume results/campaign-20260918-1200

The campaign flashes both boards **once** and then drives everything over the serial command
protocol, so almost all of the wall clock is spent measuring rather than rebuilding.

Two design points worth knowing before changing anything here:

* **Repetitions are interleaved, never grouped** (practice P9). The schedule is repetition-major
  and rotates technologies between repetitions, so a configuration point's repeats are separated
  by the entire rest of the campaign. A transient disturbance then costs every configuration a
  little rather than destroying one completely.
* **LoRa airtime is paced by the host**, not by the device. The firmware rebuilds its Transport on
  every CFG, which constructs a fresh DutyCycleGuard and discards its ledger - so the device
  genuinely cannot enforce an hourly limit across configuration points, and the host must.
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench import technologies
from bench.bringup import resolve_ports, start_power
from bench.campaign import CampaignRunner
from bench.campaign_loop import run_schedule
from bench.schedule import PRESETS, allocate, build_schedule
from bench.device import Device
from bench.ports import ensure_pyserial, flash, read_mac_for_port
from bench.power import DEFAULT_VOLTAGE_MV
from bench.results import ResultWriter, write_meta
from bench.util import DEFAULT_ZIGBEE_PEER, format_duration, git_sha

REPO_ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--preset", choices=sorted(PRESETS), default="standard")
    parser.add_argument("--budget-hours", type=float,
                         help="override the preset's wall-clock budget")
    parser.add_argument("--forever", action="store_true",
                         help="keep rotating until Ctrl-C. Every rotation is a complete pass over "
                              "all three technologies, so stopping at any point leaves a balanced "
                              "dataset - more repetitions, never partial coverage")
    parser.add_argument("--techs", nargs="+", default=technologies.ALL, choices=technologies.ALL)
    parser.add_argument("--gateway-port")
    parser.add_argument("--node-port")
    parser.add_argument("--zigbee-peer", default=DEFAULT_ZIGBEE_PEER)
    parser.add_argument("--pio", default="pio")
    parser.add_argument("--out", help="results directory (default: results/campaign-<timestamp>)")
    parser.add_argument("--resume", help="continue an existing campaign directory")
    parser.add_argument("--skip-flash", action="store_true",
                         help="the boards already run the current firmware")
    parser.add_argument("--dry-run", action="store_true",
                         help="print the plan and the schedule, touch no hardware")
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
    parser.add_argument("--control-campaign",
                         help="name of an earlier campaign this one should be compared against "
                              "(e.g. a pre-strip run, to show the strip changed only power)")
    args = parser.parse_args()

    # Before any output: this may re-exec under PlatformIO's interpreter, and on a terminal
    # (line-buffered) anything already printed would then appear twice.
    ensure_pyserial(args.pio)

    # "Forever" is a very large budget rather than an unbounded loop, so the schedule is still
    # built once, up front, by the same repetition-major allocator. That keeps the rotation
    # structure, the point-order rotation and --resume identical to a fixed-budget campaign;
    # only the stopping condition changes, from exhausting the schedule to Ctrl-C.
    FOREVER_HOURS = 24 * 30.0
    budget_hours = FOREVER_HOURS if args.forever else (args.budget_hours or PRESETS[args.preset])
    plan = allocate(budget_hours, args.techs)

    if args.forever:
        print(f"running until Ctrl-C across {', '.join(args.techs)} "
              f"(schedule built for {budget_hours / 24:.0f} days; stop whenever)")
    else:
        print(f"budget: {budget_hours:.1f} h across {', '.join(args.techs)}")
    total_estimate = 0.0
    for tech in args.techs:
        entry = plan[tech]
        total_estimate += entry["estimated_total_s"]
        note = ""
        if entry["airtime_per_repetition_s"]:
            note = (f", airtime {entry['airtime_per_repetition_s']:.0f}s/rep "
                    f"-> duty-cycle floor {entry['airtime_per_repetition_s'] / 0.10 / 60:.0f} min/rep")
        print(f"  {tech:<8} {entry['points']:>3} points x {entry['repetitions']:>2} repetitions"
              f"  ({format_duration(entry['estimated_total_s'])}{note})")
    print(f"  estimated total: {format_duration(total_estimate)}")

    schedule = build_schedule(plan, args.techs)
    print(f"  {len(schedule)} configuration points scheduled")

    if args.dry_run:
        print("\nfirst 12 scheduled runs (note the interleaving — practice P9):")
        for run in schedule[:12]:
            print(f"  #{run.index:<4} rot={run.rotation}  {run.point.key}")
        return 0

    out_dir = Path(args.resume) if args.resume else Path(
        args.out or REPO_ROOT / "results" / f"campaign-{datetime.now():%Y%m%d-%H%M%S}")
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # Rail first, then the boards: see bench/bringup.py for why that order is forced. The monitor
    # has to span the whole campaign, because DUT power drops when its session closes.
    power = start_power(args, out_dir / "power_traces")
    gw_port, node_port = resolve_ports(args)
    print(f"\ngateway {gw_port}\nnode    {node_port}")

    # Two flashes for the entire campaign. Everything after this is runtime configuration.
    if not args.skip_flash:
        print("\nflashing (once for the whole campaign)...")
        flash(args.pio, "bench_node", node_port)
        flash(args.pio, "bench_gateway", gw_port)

    peer = {"mac": read_mac_for_port(node_port, args.pio), "xbee64": args.zigbee_peer}
    gateway_mac = read_mac_for_port(gw_port, args.pio)
    print(f"peer MAC for the gateway to target: {peer['mac']}")

    writer = ResultWriter(out_dir)
    already_done = writer.completed_indices() if args.resume else set()
    if already_done:
        print(f"resuming: {len(already_done)} of {len(schedule)} points already recorded")

    with Device(gw_port, name="gw") as gateway, Device(node_port, name="node") as node:
        # The node needs the *gateway's* MAC as its ESP-NOW peer, and vice versa.
        node_peer = {"mac": gateway_mac, "xbee64": args.zigbee_peer}

        print(f"gateway says: {gateway.ping()}   node says: {node.ping()}")
        device_info = {"gateway": gateway.info(), "node": node.info()}

        # `power` was started above, before the boards were touched. The metadata below still
        # records whether energy was actually being captured rather than whether it was asked for.
        write_meta(out_dir, {
            "started_at": time.time(),
            "started_at_iso": datetime.now().isoformat(timespec="seconds"),
            "preset": args.preset,
            "budget_hours": budget_hours,
            "techs": args.techs,
            "plan": plan,
            "scheduled_points": len(schedule),
            "gateway_port": gw_port,
            "node_port": node_port,
            "peer": peer,
            "device_info": device_info,
            "power": ({"mode": args.power_mode, "voltage_mv": args.power_voltage_mv,
                        "measured_board": "node"} if power else None),
            "control_campaign": args.control_campaign,
            # The single fact that decides whether this campaign may be pooled with another.
            # Capturing signal quality perturbs ESP-NOW (promiscuous sniffer) and Zigbee (a UART
            # round trip per receive), so a campaign recorded with it on measures a different
            # system from one recorded without.
            "rssi_capture": bool(args.rssi),
            "benchmark_git_sha": git_sha(REPO_ROOT),
            "connectionlib_git_sha": git_sha(REPO_ROOT / "ConnectionLib"),
            # Practice P14: record the full configuration space, not only what was reached.
            "configuration_space": {
                tech: [{"factor": p.factor, "level": p.level, "params": p.params}
                        for p in technologies.get(tech).points()]
                for tech in args.techs
            },
        })

        runner = CampaignRunner(gateway, node, peer, node_peer, out_dir, writer,
                                 out_dir / "status.json", power=power, capture_rssi=args.rssi)
        started = time.time()
        interrupted = run_schedule(runner, schedule, already_done, started)

        runner._write_status(schedule)
        if power:
            power.stop()

    writer.close()

    meta = json.loads((out_dir / "meta.json").read_text())
    meta["finished_at"] = time.time()
    meta["elapsed_s"] = round(time.time() - started, 1)
    meta["failures"] = runner.failures
    meta["lora_airtime_total_s"] = round(runner.airtime.total_s, 1)
    if power:
        meta["power_samples_total"] = power.total_samples
    meta["duty_wait_total_s"] = round(runner.duty_wait_total_s, 1)
    meta["interrupted"] = interrupted
    write_meta(out_dir, meta)

    verb = "stopped after" if interrupted else "finished in"
    print(f"\ncampaign {verb} {format_duration(meta['elapsed_s'])}")
    print(f"  results: {out_dir}")
    print(f"  {runner.completed} of {len(schedule)} points recorded")
    print(f"  LoRa airtime: {runner.airtime.total_s:.0f}s total, "
          f"{runner.duty_wait_total_s:.0f}s spent waiting on the duty cycle")
    if runner.failures:
        print(f"  {len(runner.failures)} point(s) failed or were incomplete")
    if interrupted:
        # Repetition-major order means whatever was reached is a balanced prefix: every rotation
        # is a complete pass over all three technologies, so stopping early costs repetitions
        # rather than coverage.
        print(f"\nresume where this left off:\n  ./run_campaign.py --resume {out_dir}")
    print(f"\nanalyse what you have:\n  ./verify_practices.py --results {out_dir}"
          f"\n  ./plot_results.py --results {out_dir}")
    return 130 if interrupted else (1 if runner.failures else 0)


if __name__ == "__main__":
    sys.exit(main())

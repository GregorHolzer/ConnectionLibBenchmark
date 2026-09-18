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
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench import technologies
from bench.campaign import PRESETS, CampaignRunner, allocate, build_schedule
from bench.device import Device, detect_ports, ensure_pyserial, flash, read_mac_for_port
from bench.power import DEFAULT_VOLTAGE_MV, PowerMonitor
from bench.results import ResultWriter, write_meta

REPO_ROOT = Path(__file__).resolve().parent

# The bench's XBee node module (see ConnectionLib docs/hardware.md). There is no host-side way to
# read an XBee's address without talking to the module, so unlike the ESP-NOW MAC - which esptool
# reads straight off the ROM loader - this one is supplied. Swap the boards and every Zigbee
# send() will time out; pass --zigbee-peer to correct it.
DEFAULT_ZIGBEE_PEER = "0013A20042035B39"


def git_sha(path):
    try:
        return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                               capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def format_duration(seconds):
    if seconds is None:
        return "?"
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}h{minutes:02d}m" if hours else f"{minutes}m{secs:02d}s"


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--preset", choices=sorted(PRESETS), default="standard")
    parser.add_argument("--budget-hours", type=float,
                         help="override the preset's wall-clock budget")
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
                         help="source-meter voltage; 3900 is Li-ion mid-discharge")
    parser.add_argument("--power-mode", choices=["source", "ampere"], default="source",
                         help="source: PPK2 powers the node from its battery terminal. ampere: "
                              "PPK2 sits in series with an externally powered rail")
    parser.add_argument("--no-power", action="store_true",
                         help="skip energy measurement even if a PPK2 is attached")
    parser.add_argument("--control-campaign",
                         help="name of an earlier campaign this one should be compared against "
                              "(e.g. a pre-strip run, to show the strip changed only power)")
    args = parser.parse_args()

    budget_hours = args.budget_hours or PRESETS[args.preset]
    plan = allocate(budget_hours, args.techs)

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

    ensure_pyserial(args.pio)

    out_dir = Path(args.resume) if args.resume else Path(
        args.out or REPO_ROOT / "results" / f"campaign-{datetime.now():%Y%m%d-%H%M%S}")
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    gw_port, node_port = args.gateway_port, args.node_port
    if not gw_port or not node_port:
        detected = detect_ports(args.pio)
        if len(detected) != 2:
            sys.exit(f"expected exactly 2 serial devices, found {len(detected)}: {detected}")
        gw_port = gw_port or detected[0]
        node_port = node_port or detected[1]
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

        # Started before the metadata is written so the record says whether energy was actually
        # being captured, rather than whether it was asked for.
        power = None if args.no_power else PowerMonitor.create(
            port=args.power_port, voltage_mv=args.power_voltage_mv, mode=args.power_mode,
            raw_dir=out_dir / "power_traces")

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
                                 out_dir / "status.json", power=power)
        started = time.time()

        for run in schedule:
            if run.index in already_done:
                runner.completed += 1
                continue

            # Idle points have no technology module — the board floor is not any one radio's, and
            # they exchange nothing, so expected-count reporting does not apply to them.
            is_idle = run.point.factor == "idle"
            module = None if is_idle else technologies.get(run.tech)
            elapsed = time.time() - started
            print(f"[{runner.completed + 1}/{len(schedule)}] {format_duration(elapsed)} "
                  f"rot={run.rotation} {run.point.key}", flush=True)
            runner._write_status(schedule, current=run.point.key)

            try:
                ok, exchanges, summary = runner.run_one(run)
            except Exception as exc:                     # noqa: BLE001 - a ten-hour run must not
                print(f"    FAILED: {exc}")              # die because one point misbehaved
                runner.failures.append({"index": run.index, "point": run.point.key,
                                         "error": str(exc)})
                runner.completed += 1
                continue

            if is_idle:
                held = summary.get("elapsed_ms", 0) / 1000.0 if summary else 0.0
                print(f"    idle held {held:.0f}s")
            elif ok and exchanges:
                rtts = sorted(e["rtt_us"] for e in exchanges if e["ok"])
                median = rtts[len(rtts) // 2] / 1000.0 if rtts else 0.0
                print(f"    ok  {summary['successes']}/{module.exchanges(run.point)}  "
                      f"median rtt {median:.2f} ms  retries {summary['retries']}")
                runner.recent.append({"point": run.point.key, "rtt_ms": round(median, 3)})
            else:
                got = summary["successes"] if summary else 0
                print(f"    INCOMPLETE {got}/{module.exchanges(run.point)}")

            runner.completed += 1
            runner._write_status(schedule)

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
    write_meta(out_dir, meta)

    print(f"\ncampaign finished in {format_duration(meta['elapsed_s'])}")
    print(f"  results: {out_dir}")
    print(f"  LoRa airtime: {runner.airtime.total_s:.0f}s total, "
          f"{runner.duty_wait_total_s:.0f}s spent waiting on the duty cycle")
    if runner.failures:
        print(f"  {len(runner.failures)} point(s) failed or were incomplete")
    print(f"\nnext:\n  ./verify_practices.py --results {out_dir}"
          f"\n  ./plot_results.py --results {out_dir}")
    return 1 if runner.failures else 0


if __name__ == "__main__":
    sys.exit(main())

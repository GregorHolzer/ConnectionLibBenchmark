#!/usr/bin/env python3
"""Distance benchmark for ConnectionLib: flash the node per configuration, record what it measures.

    ./bench.py prepare --node-port ... --gateway-port ... --zigbee-peer 0013A200...
        Once, at the desk, both boards on USB: read their addresses, flash the gateway, build the
        node firmware so the field needs no network.

    ./bench.py baseline
        Once: board floor and the standing current of each radio. Distance does not matter.

    ./bench.py site --distance 100
        At every spot: Zigbee, ESP-NOW, then every LoRa setting. Run it again to resume.

    ./bench.py status
        What has been recorded so far, as a table.

All sites of one day share a campaign directory (results/field-<date> unless --campaign is given).
--firmware points at another firmware tree, e.g. ../ConnectionLibBenchmarkRaw.
"""

import argparse
import json
import random
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

from bench import airtime, configs, firmware, results, summary
from bench.runner import Runner, describe

REPO_ROOT = Path(__file__).resolve().parent
LEDGER = REPO_ROOT / "results" / "lora_airtime_ledger.txt"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--firmware", type=Path, default=REPO_ROOT,
                        help="firmware project directory (default: this repository)")
    parser.add_argument("--campaign", default=f"field-{date.today():%Y%m%d}",
                        help="directory under results/ (default: field-<today>)")
    parser.add_argument("--pio", default="pio")
    parser.add_argument("--no-power", action="store_true", help="run without the PPK2")
    parser.add_argument("--ignore-duty-cycle", action="store_true",
                        help="development only: do not wait between runs for the 433 MHz duty "
                             "cycle. Airtime is still logged; the campaign is marked.")
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare", help="record the boards and flash the gateway")
    prepare.add_argument("--node-port", required=True)
    prepare.add_argument("--gateway-port", required=True)
    prepare.add_argument("--zigbee-peer", required=True,
                         help="64-bit address of the gateway's XBee, hex (the coordinator)")

    commands.add_parser("baseline", help="board floor and standing currents")

    site = commands.add_parser("site", help="measure every configuration at one distance")
    site.add_argument("--distance", type=float, required=True, help="metres to the gateway")
    site.add_argument("--rounds", type=int, default=1,
                      help="repeat the set; even rounds run in reverse order")
    site.add_argument("--tech", action="append", choices=["zigbee", "espnow", "lora"],
                      help="limit to these technologies (repeatable)")
    site.add_argument("--config", action="append", metavar="KEY",
                      choices=[c.key for c in configs.site_configs()],
                      help="limit to these configurations, e.g. zigbee/payload=512 (repeatable)")
    site.add_argument("--note", default="", help="free text: weather, obstacles, antenna height")

    commands.add_parser("status", help="print what the campaign holds")
    return parser.parse_args()


def firmware_facts(project):
    """What the firmware tree says about itself; this repository is the library stack."""
    path = Path(project) / "bench_firmware.json"
    facts = {"stack": "lib", "lora_header_bytes": airtime.LORA_HEADER_BYTES}
    if path.exists():
        facts.update(json.loads(path.read_text()))
    return facts


def prepare(args):
    boards = {
        "node_port": args.node_port,
        "node_mac": firmware.read_mac(args.node_port, args.pio),
        "gateway_mac": firmware.read_mac(args.gateway_port, args.pio),
        "gateway_xbee64": args.zigbee_peer,
        "prepared_at": datetime.now().isoformat(timespec="seconds"),
    }
    firmware.BOARDS_FILE.write_text(json.dumps(boards, indent=2) + "\n")
    print(f"node {boards['node_mac']}, gateway {boards['gateway_mac']} -> {firmware.BOARDS_FILE.name}")

    idle = configs.Config("none", "floor")
    print("flashing the gateway ...")
    firmware.flash(args.firmware, "gateway", args.gateway_port,
                   firmware.render_header(idle, boards["node_mac"], args.zigbee_peer, 0), args.pio)
    print("building the node firmware ...")
    subprocess.run([args.pio, "run", "-d", str(args.firmware), "-e", "node"], check=True,
                   capture_output=True)
    print("ready: the gateway can be unplugged and placed")


def make_runner(args):
    from bench.power import PowerMonitor   # imported late: needs ppk2-api only when measuring

    boards = firmware.load_boards()
    out_dir = REPO_ROOT / "results" / args.campaign
    writer = results.ResultWriter(out_dir)
    facts = firmware_facts(args.firmware)
    writer.write_meta({"campaign": args.campaign, "boards": boards, "firmware": facts,
                       "duty_cycle_enforced": not args.ignore_duty_cycle,
                       "started_at": datetime.now().isoformat(timespec="seconds")})
    power = None if args.no_power else PowerMonitor.create(raw_dir=out_dir / "raw")
    if power is None and not args.no_power:
        sys.exit("no PPK2: fix it, or pass --no-power to record without energy")
    # The PPK2 is the node's supply, and the board's USB-serial chip runs off that rail: the port
    # only appears a second or two after the rail comes up.
    deadline = time.time() + 20
    while not Path(boards["node_port"]).exists():
        if time.time() > deadline:
            sys.exit(f"node port {boards['node_port']} did not appear")
        time.sleep(0.25)
    budget = airtime.AirtimeBudget(LEDGER, enforce=not args.ignore_duty_cycle)
    if args.ignore_duty_cycle:
        print("duty cycle NOT enforced between runs (--ignore-duty-cycle): development use only")
    runner = Runner(args.firmware, boards["node_port"], boards, writer, budget, power, args.pio,
                    facts["lora_header_bytes"])
    return runner, writer, power


def run_all(runner, writer, plan):
    """Run every (config, distance, round) not yet recorded. Ctrl-C stops between runs cleanly.

    Once a Zigbee or ESP-NOW baseline has delivered nothing at a distance, that radio's other
    points there are skipped: each would only fail and cost its lease. The latest baseline
    decides, and a baseline itself is never skipped, so a later round can test the radio again.
    LoRa always runs."""
    done = writer.completed()
    out_of_reach = set()

    def note_reach(run):
        if run["name"] == "baseline" and run["summary"] and not run["error"]:
            key = (run["distance_m"], run["tech"])
            out_of_reach.add(key) if results.unreachable(run) else out_of_reach.discard(key)

    for recorded in results.load_runs(writer.dir):
        note_reach(recorded)
    todo = [item for item in plan if (item[1], item[2], item[0].key) not in done]
    print(f"{len(todo)} to run, {len(plan) - len(todo)} already recorded")
    for index, (config, distance, round_index) in enumerate(todo, 1):
        print(f"[{index}/{len(todo)}] {config.key}", flush=True)
        if config.name != "baseline" and (distance, config.tech) in out_of_reach:
            print("  skipped: this radio's baseline delivered nothing at this distance", flush=True)
            continue
        try:
            record = runner.run(config, distance, round_index)
        except firmware.FirmwareError as exc:
            sys.exit(str(exc))
        print(f"  {describe(record)}", flush=True)
        note_reach(record)


def site_plan(args):
    """The three baselines (Zigbee, ESP-NOW, LoRa), then every swept point in an order shuffled
    per distance, so no setting always runs last. Even rounds reverse the whole order, which puts
    any drift during the visit on both sides of every configuration."""
    order = configs.site_configs(args.tech or ("zigbee", "espnow", "lora"))
    if args.config:
        order = [c for c in order if c.key in args.config]
    fixed = [c for c in order if c.name == "baseline"]
    swept = [c for c in order if c not in fixed]
    random.Random(f"{args.campaign}/{args.distance}").shuffle(swept)
    plan = []
    for round_index in range(args.rounds):
        ordered = fixed + swept
        plan += [(config, args.distance, round_index)
                 for config in (reversed(ordered) if round_index % 2 else ordered)]
    return plan


def main():
    args = parse_args()
    if args.command == "prepare":
        return prepare(args)
    if args.command == "status":
        out_dir = REPO_ROOT / "results" / args.campaign
        runs = results.load_runs(out_dir)
        standing = summary.standing_currents(runs)
        for tech, ua in standing.items():
            print(f"standing {tech:<7} {ua / 1000:7.2f} mA")
        return print(summary.table(summary.summarise(runs, results.load_exchanges(out_dir))))

    runner, writer, power = make_runner(args)
    try:
        if args.command == "baseline":
            run_all(runner, writer, [(c, None, 0) for c in configs.baseline_configs()])
        else:
            with (writer.dir / "sites.jsonl").open("a") as handle:
                handle.write(json.dumps({"distance_m": args.distance, "note": args.note,
                                         "at": datetime.now().isoformat(timespec="seconds")}) + "\n")
            run_all(runner, writer, site_plan(args))
    except KeyboardInterrupt:
        print("\nstopped; run the same command again to resume")
    finally:
        if power:
            power.stop()


if __name__ == "__main__":
    main()

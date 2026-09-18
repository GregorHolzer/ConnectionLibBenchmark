#!/usr/bin/env python3
"""Show what a running (or finished) campaign is doing.

    ./progress.py                      # newest campaign in results/
    ./progress.py --watch              # refresh every 5s
    ./progress.py --results results/campaign-20260918-1200

Safe to run against a live campaign: it only reads `status.json`, which the runner rewrites after
every configuration point. It never touches the serial ports, so it cannot disturb a measurement -
which matters, because practice P5 is about not perturbing the thing being measured.
"""

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from bench import technologies                                   # noqa: E402
from bench.campaign import DUTY_CYCLE_FRACTION, POINT_OVERHEAD_S, build_schedule   # noqa: E402

BAR_WIDTH = 42
GREEN, YELLOW, RED, DIM, BOLD, RESET = (
    "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[1m", "\033[0m")


def newest_campaign(results_root):
    candidates = [p for p in Path(results_root).glob("*") if (p / "status.json").exists()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: (p / "status.json").stat().st_mtime)


def duration(seconds):
    if seconds is None:
        return "?"
    seconds = int(max(seconds, 0))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def bar(fraction, width=BAR_WIDTH):
    filled = int(round(fraction * width))
    return "█" * filled + "·" * (width - filled)


def remaining_estimate(meta, done):
    """Seconds left, from each technology's own cost model rather than a running average.

    A flat average over completed points is badly wrong here, because the points are not
    interchangeable: the schedule front-loads ESP-NOW at ~11 s a point, so an hour in the average
    predicts a 2 h campaign when LoRa alone - at a duty-cycle floor of 28 minutes per repetition -
    still has five hours to go. Re-deriving the schedule and summing what has not run yet is both
    simple and honest.
    """
    plan, techs = meta.get("plan"), meta.get("techs")
    if not plan or not techs:
        return None
    try:
        schedule = build_schedule(plan, techs)
    except (KeyError, ValueError):
        return None
    if done >= len(schedule):
        return 0.0

    # Accumulate per technology, then take the binding constraint for each one separately and add
    # them up. The technologies share the two boards so they run sequentially, and only LoRa has a
    # duty cycle - one global max() would let LoRa's five hours of mandatory waiting absorb the
    # other two technologies' real work and under-report the total by hours.
    measured, airtime = defaultdict(float), defaultdict(float)
    for run in schedule[done:]:
        if run.point.factor == "idle":
            # Fixed hold, and no transmission at all — an idle radio is receiving, not sending,
            # so idle points cost no airtime against the duty cycle.
            measured[run.tech] += technologies.IDLE_SECONDS + POINT_OVERHEAD_S
            continue
        module = technologies.get(run.tech)
        measured[run.tech] += module.estimated_seconds(run.point) + POINT_OVERHEAD_S
        airtime[run.tech] += module.airtime_seconds(run.point)

    return sum(max(measured[tech], airtime[tech] / DUTY_CYCLE_FRACTION) for tech in measured)


def render(results_dir):
    status_path = results_dir / "status.json"
    if not status_path.exists():
        return f"no status.json in {results_dir}"
    try:
        status = json.loads(status_path.read_text())
    except json.JSONDecodeError:
        return "status.json is mid-write; try again"   # the runner rewrites it atomically enough

    meta = {}
    meta_path = results_dir / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            pass

    done, total = status["points_done"], status["points_total"]
    fraction = done / total if total else 0.0
    stale = time.time() - status.get("updated_at", 0)
    finished = "finished_at" in meta

    lines = []
    lines.append(f"{BOLD}{results_dir.name}{RESET}"
                 + (f"   {DIM}preset={meta.get('preset', '?')}{RESET}" if meta else ""))
    lines.append("")
    lines.append(f"  {bar(fraction)}  {fraction * 100:5.1f}%   {done}/{total} points")
    lines.append("")
    lines.append(f"  elapsed   {duration(status['elapsed_s'])}")
    if finished:
        lines.append(f"  {GREEN}campaign finished{RESET}")
    else:
        eta = remaining_estimate(meta, done)
        if eta is None:
            eta = status.get("eta_s")
        lines.append(f"  remaining {duration(eta)}"
                     + (f"   {DIM}(done ~{datetime.fromtimestamp(time.time() + eta):%H:%M}){RESET}"
                        if eta else ""))
        freshness = GREEN if stale < 120 else (YELLOW if stale < 600 else RED)
        lines.append(f"  updated   {freshness}{duration(stale)} ago{RESET}")

    if status.get("current") and not finished:
        lines.append(f"  running   {BOLD}{status['current']}{RESET}")

    # Repetitions per technology. This is the number that decides whether the reliability analysis
    # can say anything at all - a technology with one repetition cannot separate random from
    # systematic error (practice P2).
    rotations = status.get("rotations_done") or {}
    if rotations:
        lines.append("")
        lines.append("  repetitions completed")
        plan = (meta.get("plan") or {})
        for tech in sorted(rotations):
            target = plan.get(tech, {}).get("repetitions")
            colour = GREEN if rotations[tech] >= 3 else YELLOW
            suffix = f" / {target}" if target else ""
            note = "" if rotations[tech] >= 3 else f"  {DIM}(3+ needed for reliability){RESET}"
            lines.append(f"    {tech:<8} {colour}{rotations[tech]}{suffix}{RESET}{note}")

    # LoRa airtime against the legal budget. Not a nicety: it is the campaign's real currency, and
    # a large wait is the duty cycle being enforced rather than something being stuck.
    used = status.get("lora_airtime_used_s", 0.0)
    limit = status.get("lora_airtime_limit_s", 360.0)
    if status.get("lora_airtime_total_s"):
        share = used / limit if limit else 0.0
        colour = GREEN if share < 0.8 else YELLOW
        lines.append("")
        lines.append("  LoRa duty cycle (rolling hour, ETSI 10 %)")
        lines.append(f"    {bar(min(share, 1.0), 28)} {colour}{used:5.0f}s{RESET} / {limit:.0f}s")
        lines.append(f"    {DIM}{status['lora_airtime_total_s']:.0f}s airtime all campaign, "
                     f"{duration(status.get('duty_wait_total_s', 0))} spent waiting on it{RESET}")

    recent = status.get("recent") or []
    if recent:
        lines.append("")
        lines.append("  recent points")
        for entry in recent[-6:]:
            lines.append(f"    {DIM}{entry['point']:<34}{RESET} {entry['rtt_ms']:>9.2f} ms")

    failures = status.get("failures") or []
    if failures:
        lines.append("")
        lines.append(f"  {RED}{len(failures)} failure(s){RESET}")
        for failure in failures[-4:]:
            detail = failure.get("error") or \
                f"{failure.get('successes')}/{failure.get('expected')} exchanges"
            lines.append(f"    {RED}{failure['point']:<34}{RESET} {detail}")

    if finished:
        lines.append("")
        lines.append(f"  next:  ./verify_practices.py --results {results_dir}")
        lines.append(f"         ./plot_results.py     --results {results_dir}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results", help="campaign directory (default: newest under results/)")
    parser.add_argument("--watch", action="store_true", help="refresh until interrupted")
    parser.add_argument("--interval", type=float, default=5.0)
    args = parser.parse_args()

    if args.results:
        results_dir = Path(args.results)
        if not results_dir.is_absolute():
            results_dir = REPO_ROOT / results_dir
    else:
        results_dir = newest_campaign(REPO_ROOT / "results")
        if results_dir is None:
            sys.exit("no campaign found under results/ - run ./run_campaign.py first")

    if not args.watch:
        print(render(results_dir))
        return 0

    try:
        while True:
            print("\033[2J\033[H" + render(results_dir), flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print()
        return 0


if __name__ == "__main__":
    sys.exit(main())

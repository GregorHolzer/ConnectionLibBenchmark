"""The campaign's main loop, and the two kinds of interruption it has to survive.

Extracted from run_campaign.py, where it was the bulk of a 225-line main(). Everything here
exists because a ten-hour run must not be lost to one bad point:

* **A failed point resynchronises both boards before the next one starts.** A board that overran
  its timeout has not stopped - the firmware retries every exchange up to five times, so it can
  still be transmitting for minutes. Sending the next CFG into that stream gets no ACK, and the
  *following* point then fails for a reason that has nothing to do with its own configuration.
  This is what keeps one marginal point from taking the next six with it.
* **Ctrl-C mid-point discards that point rather than recording it.** It was cut mid-exchange, so
  its energy window and its exchange count are both short. Only completed points reach
  points.jsonl, so --resume re-runs it.
"""

import time

from . import technologies
from .util import format_duration


def _flushing_print(message):
    """Unbuffered by default: a campaign's output is usually piped to a log, and ten hours of
    progress that only appears at the end is not progress."""
    print(message, flush=True)


def run_schedule(runner, schedule, already_done, started, log=_flushing_print):
    """Execute every scheduled point that is not already recorded.

    Returns True if the run was interrupted. The caller owns the metadata and the summary; this
    owns only the loop and its recovery.
    """
    for run in schedule:
        if run.index in already_done:
            runner.completed += 1
            continue

        # Idle points have no technology module — the board floor is not any one radio's, and
        # they exchange nothing, so expected-count reporting does not apply to them.
        is_idle = run.point.factor == "idle"
        module = None if is_idle else technologies.get(run.tech)
        elapsed = time.time() - started
        log(f"[{runner.completed + 1}/{len(schedule)}] {format_duration(elapsed)} "
            f"rot={run.rotation} {run.point.key}")
        runner._write_status(schedule, current=run.point.key)

        try:
            ok, exchanges, summary = runner.run_one(run)
        except KeyboardInterrupt:
            # The point in flight is abandoned rather than recorded: it was cut mid-exchange,
            # so its energy window and its exchange count are both short. --resume will
            # re-run it, because only completed points are written to points.jsonl.
            log("\n    interrupted mid-point — discarding it, stopping cleanly")
            return True
        except Exception as exc:                     # noqa: BLE001 - a ten-hour run must not
            log(f"    FAILED: {exc}")                # die because one point misbehaved
            runner.failures.append({"index": run.index, "point": run.point.key,
                                     "error": str(exc)})
            _resync(runner, log)
            runner.completed += 1
            continue

        _report_point(runner, run, module, is_idle, ok, exchanges, summary, log)

        runner.completed += 1
        try:
            runner._write_status(schedule)
        except KeyboardInterrupt:
            # Ctrl-C between points: the point just finished is already flushed to disk, so
            # there is nothing to discard.
            log("\n  interrupted — stopping cleanly")
            return True
    return False


def _resync(runner, log):
    """Let both boards finish whatever they are still doing before the next CFG goes out."""
    # In field mode there is no gateway on USB; its lease expiry does the resynchronising.
    for board in (b for b in (runner.gateway, runner.node) if b is not None):
        try:
            if board.resync():
                log(f"    {board.name} finished late; stream resynchronised")
        except Exception as resync_exc:       # noqa: BLE001 - best effort by design
            log(f"    {board.name} resync failed: {resync_exc}")


def _report_point(runner, run, module, is_idle, ok, exchanges, summary, log):
    if is_idle:
        held = summary.get("elapsed_ms", 0) / 1000.0 if summary else 0.0
        log(f"    idle held {held:.0f}s")
    elif ok and exchanges:
        rtts = sorted(e["rtt_us"] for e in exchanges if e["ok"])
        median = rtts[len(rtts) // 2] / 1000.0 if rtts else 0.0
        log(f"    ok  {summary['successes']}/{module.exchanges(run.point)}  "
            f"median rtt {median:.2f} ms  retries {summary['retries']}")
        runner.recent.append({"point": run.point.key, "rtt_ms": round(median, 3)})
    else:
        got = summary["successes"] if summary else 0
        log(f"    INCOMPLETE {got}/{module.exchanges(run.point)}")

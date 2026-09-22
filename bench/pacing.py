"""How long the host waits, and why - the two answers are regulation and a marginal link.

**Airtime pacing.** LoRa's 10 % duty cycle is enforced *here*, on the host, and that is not
redundant with the on-device `DutyCycleGuard`: the firmware rebuilds its Transport on every CFG,
which constructs a fresh guard and discards its ledger, so the device cannot see across
configuration points. The host tracks a rolling one-hour window over the whole campaign and waits
when the next point would exceed it.

**Run timeouts.** A point that is retrying everything legitimately takes far longer than its
estimate, so the timeout is the worst case the firmware can actually reach rather than a multiple
of the happy path. A failure then means the point really failed, not that the host was impatient.
"""

import time
from collections import deque

# ETSI EN 300 220-2 / ERC-REC 70-03 h1.3 - see docs/regulations.md.
DUTY_CYCLE_FRACTION = 0.10
DUTY_CYCLE_WINDOW_S = 3600.0

# Mirrors BenchRunner.cpp: the firmware abandons a sequence number after this many attempts.
# Keeping the two in step is what makes run_timeout() an upper bound rather than a guess.
MAX_ATTEMPTS_PER_SEQ = 5

# Ceiling on a single point's wall clock. See run_timeout().
RUN_TIMEOUT_CAP_S = 300


class AirtimeBudget:
    """Rolling-window airtime ledger, in the host, spanning the whole campaign."""

    def __init__(self, fraction=DUTY_CYCLE_FRACTION, window_s=DUTY_CYCLE_WINDOW_S):
        self.fraction = fraction
        self.window_s = window_s
        self.limit_s = fraction * window_s
        self._entries = deque()   # (timestamp, seconds)
        self.total_s = 0.0

    def _prune(self, now):
        while self._entries and now - self._entries[0][0] > self.window_s:
            self._entries.popleft()

    def used(self, now=None):
        now = now or time.time()
        self._prune(now)
        return sum(seconds for _, seconds in self._entries)

    def record(self, seconds):
        if seconds <= 0:
            return
        self._entries.append((time.time(), seconds))
        self.total_s += seconds

    def wait_for(self, seconds, sleeper=time.sleep):
        """Block until `seconds` of additional airtime would fit inside the window.

        Returns how long it waited, which the campaign records - a large value is not a stall but
        the duty cycle being visibly enforced, and it belongs in the results.
        """
        if seconds <= 0:
            return 0.0
        waited = 0.0
        while True:
            now = time.time()
            self._prune(now)
            if self.used(now) + seconds <= self.limit_s:
                return waited
            # Wait until the oldest entry ages out of the window.
            oldest_at, _ = self._entries[0]
            pause = max(1.0, (oldest_at + self.window_s) - now + 0.5)
            sleeper(pause)
            waited += pause


def run_timeout(module, point, params):
    """How long to wait for a point before declaring it failed.

    The old formula was `estimated_seconds * 3 + 60`, which prices a run in which nothing is
    retried. But a marginal link retries everything: the firmware allows five attempts per sequence
    number, each costing a full reply-wait, so a point whose estimate is 7 s can legitimately take
    210 s. The host would give up at 81 s while the board was still working, and then send the next
    CFG into a stream that nobody was reading - which is how one bad point took the next six with
    it.

    So the timeout is the worst case the firmware can actually reach, and a failure now means the
    point really did fail rather than that the host was impatient.
    """
    exchanges = module.exchanges(point)
    reply_wait_ms = params.get("replywait", 4000)
    spacing_ms = params.get("spacing", 100)
    worst_case_s = exchanges * MAX_ATTEMPTS_PER_SEQ * (reply_wait_ms + spacing_ms) / 1000.0
    optimistic_s = module.estimated_seconds(point) * 3 + 60
    # Capped, because the true worst case is 18 minutes for ESP-NOW and 35 for Zigbee - both of
    # which are only reached when every single exchange fails five times, i.e. when the link is
    # dead and waiting longer tells you nothing you do not already know. Giving up early is safe
    # now that a failure is followed by resync(): the board is left to finish in its own time and
    # the next point starts against a clean stream rather than a busy one.
    return max(optimistic_s, min(worst_case_s + 30, RUN_TIMEOUT_CAP_S))

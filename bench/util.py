"""Small helpers shared by the campaign entry points.

These lived in `run_campaign.py` and were imported from it by `run_benchmark.py` - one CLI script
reaching into another. Neither is campaign-specific, so they sit here instead.
"""

import subprocess

# The bench's XBee node module (see ConnectionLib docs/hardware.md). There is no host-side way to
# read an XBee's address without talking to the module, so unlike the ESP-NOW MAC - which esptool
# reads straight off the ROM loader - this one is supplied. Swap the boards and every Zigbee
# send() will time out; pass --zigbee-peer to correct it.
DEFAULT_ZIGBEE_PEER = "0013A20042035B39"

# The coordinator's module, which is the field gateway's peer: in the field the node sends to the
# gateway, and the gateway must be the board carrying the coordinator (docs/field-campaign.md).
COORDINATOR_ZIGBEE_PEER = "0013A20042035C03"


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

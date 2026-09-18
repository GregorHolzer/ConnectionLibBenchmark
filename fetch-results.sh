#!/usr/bin/env bash
# Pull campaign results back from the Pi.
#
# The counterpart to sync.sh, which pushes code but deliberately never touches results/ in either
# direction. No --delete here either: fetching must not be able to destroy anything on either end.
#
#   ./fetch-results.sh                      # everything
#   ./fetch-results.sh campaign-20260918-1200   # one campaign
set -euo pipefail

PI=${PI:-raspberrypi}
SRC=${SRC:-\~/dev/ConnectionLibBenchmark/results}
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

what="${1:-}"
mkdir -p "$HERE/results"
rsync -az --progress "$PI:$SRC/${what}" "$HERE/results/"

echo "fetched into $HERE/results/${what}"

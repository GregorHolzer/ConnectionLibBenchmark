#!/usr/bin/env bash
# Push this repo to the Raspberry Pi that has the two T-Beams on USB.
#
# Same two-pass shape as ~/.local/bin/pisync for ConnectionLib-examples, and for the same reason:
# pass 2 fills the ConnectionLib submodule directory with the laptop's *live* working copy, so
# library edits are testable without committing and bumping the submodule pointer.
#
# The laptop is authoritative for *code* - `--delete` means any Pi-side source edit is discarded.
#
# `results/` is excluded in both directions, and that exclusion is load-bearing: campaign data is
# produced on the Pi and a ten-hour run is not reproducible on a whim, so a routine code push must
# never be able to delete it. Pull results back with fetch-results.sh.
#
# Resolves the host through the `raspberrypi` ssh alias, never a literal IP: the bench has already
# moved once and the address changed.
set -euo pipefail

PI=${PI:-raspberrypi}
DEST=${DEST:-\~/dev/ConnectionLibBenchmark}
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

rsync -az --delete \
      --exclude '.pio/' --exclude '__pycache__/' --exclude '/ConnectionLib/' --exclude '.git/' \
      --exclude '/results/' \
      "$HERE/" "$PI:$DEST/"

rsync -az --delete \
      --exclude '.pio/' --exclude 'sources/' --exclude '.git/' \
      "$HOME/ConnectionLib/" "$PI:$DEST/ConnectionLib/"

echo "synced to $PI:$DEST"

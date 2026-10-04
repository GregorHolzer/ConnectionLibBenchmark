#!/usr/bin/env bash
#
# Prepare a laptop to be the field host. Run it once, at home, with network.
#
#     curl -fsSL <this file> | bash          # or, if you already have the repo:
#     ./tools/field_laptop_setup.sh
#
# Shell rather than Python, unlike everything else host-side here, for one reason: it has to run
# before the repo exists, so it cannot import bench/. Everything after the clone is Python.
#
# Idempotent: re-running updates the checkout instead of re-cloning, and pip skips what is there.
#
# What it is actually for. The field has no network, and a PlatformIO project downloads its
# library dependencies on first build - so a laptop that has never built *this* project cannot
# build it at the site. The prebuild at the end is the point of the script; the rest is what that
# prebuild needs. Two specifics, both found on the ThinkPad rather than guessed:
#
#   * The PlatformIO venv had platformio and pyserial but not ppk2-api. On the bench a missing
#     ppk2-api costs the energy columns; in source mode the PPK2 is the node's only supply, so it
#     costs the whole run - run_campaign.py exits at start-up. It must go in *that* venv, because
#     bench/ports.py:ensure_pyserial() re-execs into it.
#   * ConnectionLibBenchmark pins ConnectionLib at a commit that lives on a feature branch. A
#     plain `git clone` without --recurse-submodules leaves an empty directory, and the build then
#     fails with PlatformIO's MissingPackageManifestError, which reads like a flashing fault.
#
set -euo pipefail

REPO_URL="git@github.com:GregorHolzer/ConnectionLibBenchmark.git"
BRANCH="feat/field-campaign"
REPO_DIR="${REPO_DIR:-$HOME/ConnectionLibBenchmark}"
VENV="${VENV:-$HOME/.venvs/platformio}"

RED=$'\033[31m'; YELLOW=$'\033[33m'; GREEN=$'\033[32m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
step() { printf '\n%s==> %s%s\n' "$BOLD" "$1" "$RESET"; }
ok()   { printf '  %sok%s    %s\n' "$GREEN" "$RESET" "$1"; }
warn() { printf '  %swarn%s  %s\n' "$YELLOW" "$RESET" "$1"; }
die()  { printf '  %sfail%s  %s\n' "$RED" "$RESET" "$1" >&2; exit 1; }

step "serial access"
# Arch puts ttyACM* in uucp; Debian and Fedora use dialout. Both the boards and the PPK2 are
# CDC-ACM, so one group covers everything and no udev rule is needed.
if id -nG | grep -qwE 'uucp|dialout'; then
    ok "$(id -un) is in $(id -nG | tr ' ' '\n' | grep -E '^(uucp|dialout)$' | paste -sd,)"
else
    die "not in uucp or dialout — serial ports will be unreadable.
        Arch:   sudo usermod -aG uucp $(id -un)      then log out and back in
        Debian: sudo usermod -aG dialout $(id -un)"
fi

step "PlatformIO"
[ -x "$VENV/bin/pio" ] || die "no PlatformIO at $VENV/bin/pio.
        python -m venv $VENV && $VENV/bin/pip install platformio
        then: ln -s $VENV/bin/pio ~/.local/bin/pio"
ok "$("$VENV/bin/pio" --version)"
if command -v pio >/dev/null 2>&1 && [ "$(readlink -f "$(command -v pio)")" = "$(readlink -f "$VENV/bin/pio")" ]; then
    ok "pio on PATH -> $VENV/bin/pio"
else
    warn "pio is not on PATH as that venv. The scripts default to --pio pio:
        ln -s $VENV/bin/pio ~/.local/bin/pio"
fi

step "host packages"
# pyserial comes with platformio, but pin it anyway so a future PlatformIO that drops the
# dependency does not silently take the campaign with it.
"$VENV/bin/pip" install --quiet --upgrade ppk2-api pyserial matplotlib
"$VENV/bin/python" - <<'PY'
import importlib, sys
for name, why in (("serial", "talking to the boards"),
                  ("ppk2_api", "powering and metering the node"),
                  ("matplotlib", "the plotting scripts, afterwards")):
    importlib.import_module(name)
    print(f"  \033[32mok\033[0m    {name} — {why}")
PY

step "repository"
if [ -d "$REPO_DIR/.git" ]; then
    git -C "$REPO_DIR" fetch --quiet origin "$BRANCH"
    git -C "$REPO_DIR" checkout --quiet "$BRANCH"
    git -C "$REPO_DIR" merge --quiet --ff-only "origin/$BRANCH"
    ok "updated $REPO_DIR"
else
    git clone --quiet --branch "$BRANCH" --recurse-submodules "$REPO_URL" "$REPO_DIR"
    ok "cloned $REPO_DIR at $BRANCH"
fi
# Separately from the clone: an existing checkout's submodule may be stale or uninitialised, and
# an uninitialised one fails the build with an error that does not mention submodules.
git -C "$REPO_DIR" submodule update --init --recursive --quiet
submodule_state=$(git -C "$REPO_DIR" submodule status)
case "$submodule_state" in
    -*) die "the ConnectionLib submodule is still uninitialised: $submodule_state" ;;
    *)  ok "submodule $submodule_state" ;;
esac
ok "benchmark at $(git -C "$REPO_DIR" rev-parse --short HEAD), ConnectionLib at $(git -C "$REPO_DIR/ConnectionLib" rev-parse --short HEAD)"

step "prebuilding both firmwares (this is the step that needs network)"
cd "$REPO_DIR"
for env in field_node field_gateway; do
    "$VENV/bin/pio" run -e "$env" >/tmp/field_setup_$env.log 2>&1 \
        || die "$env failed to build; see /tmp/field_setup_$env.log"
    size=$(stat -c%s ".pio/build/$env/firmware.bin")
    ok "$env built, $((size / 1024)) KiB"
done
ok "library dependencies cached: $(ls .pio/libdeps/field_node | paste -sd' ')"

printf '\n%sready.%s The field needs no network from here on.\n' "$BOLD" "$RESET"
cat <<EOF

  Next, in this order (docs/field-runbook.md has the detail and the wiring):

    1. gateway on USB, on whichever machine:   tools/field_gateway.py flash
                                               tools/field_gateway.py status
    2. node + PPK2 on this laptop:             ./tools/bench_board.py --once
    3. the plan, no hardware:                  ./run_field.py --site "desk, 0 m" \\
                                                   --techs lora --budget-hours 0.3 --dry-run
    4. the short first run:                    ./run_field.py --site "desk, 0 m" \\
                                                   --techs lora --budget-hours 0.3 \\
                                                   --gateway-mac <from step 1>
EOF

# ConnectionLibBenchmark

Distance benchmark for ConnectionLib: a node firmware that is re-flashed per configuration, a
gateway firmware that is flashed once, and a Python runner. Start with `README.md`;
`docs/scripts.md` lists every command and flag, `docs/metrics.md` every recorded field.

## Ponytail

This repository is worked on with ponytail at level `full` (enabled in `.claude/settings.json`):
the smallest change that fully solves the task, deletion before addition, no option or
abstraction nobody asked for.

- Before committing a change: `/ponytail-review`.
- Before a cleanup: `/ponytail-audit`. It only reports; remove something only if grep shows no
  caller in `bench/`, `bench.py`, `plot.py`, `tools/` and `tests/`.
- A shortcut with a known limit gets a one-line `ponytail:` comment naming the limit;
  `/ponytail-debt` lists them.

### Never cut these

Ponytail must not remove "the calibration real hardware needs". Here that is:

- the EU limits as `static_assert`s in `src/Config.hpp`, and the gateway's `acceptable()` check;
- the duty-cycle ledger (`bench/airtime.py`), and `--ignore-duty-cycle` staying off by default;
- the PPK2 stream-latency measurement and window shift, and the 1 ms reader interval
  (`bench/power.py`): without them every energy window is seconds off;
- the `READY` / `G` start gate between node and runner;
- cutting the LoRa rail for ESP-NOW and Zigbee runs (`src/Board.cpp`);
- the gateway's lease, and the runner waiting it out after an unacknowledged end message.

### Do not change without a hardware run

`src/`, the run sequence in `bench/runner.py`, and the windowing in `bench/power.py`. A change
there is only finished after a desk run with both boards
(`bench.py --campaign <name> --ignore-duty-cycle site --distance 0`). If the boards are not
attached, say so instead of claiming it works.

## House rules

- Every source file stays at or under 250 lines.
- Python runs with `~/.venvs/platformio/bin/python`. Tests: `python -m unittest discover tests`
  (standard library only, no pytest).
- Firmware check: `pio run -e node -e gateway`.
- Conventional commits (`feat:`, `fix:`, `refactor:`, `docs:`, `chore:`).
- `src/node.cpp`, `src/NodeControl.*`, `src/gateway.cpp`, `src/Frames.hpp`, `src/Board.*` and
  `src/Transports.hpp` are copied into ConnectionLibBenchmarkRaw and must stay identical there.
- `results/`, `figures/`, `boards.json` and `include/bench_config.h` are generated and ignored by
  git. `bench/figures/` is untracked older work: leave it alone.

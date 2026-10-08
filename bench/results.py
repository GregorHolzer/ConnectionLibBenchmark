"""What a campaign leaves on disk, written as it happens.

    results/<campaign>/
        meta.json        boards, firmware stack, when it started
        runs.jsonl       one line per flashed configuration: where, what, delivery, energy
        exchanges.csv    one row per ping, for any statistic nobody thought of in advance
        serial.log       every record line the node printed, verbatim
        raw/             PPK2 waveforms of the baseline points

A campaign is one directory for all distances. Every run is appended and flushed at once, so an
interrupted day loses at most the run in progress, and `completed()` is what lets it resume.
"""

import csv
import json
from pathlib import Path

from .records import EXCHANGE_FIELDS

RUN_KEY_FIELDS = ["distance_m", "round", "key"]
CSV_FIELDS = RUN_KEY_FIELDS + EXCHANGE_FIELDS + ["status_name"]


class ResultWriter:
    def __init__(self, out_dir):
        self.dir = Path(out_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._runs = self.dir / "runs.jsonl"
        self._csv = self.dir / "exchanges.csv"
        self._serial = self.dir / "serial.log"
        if not self._csv.exists():
            with self._csv.open("w", newline="") as handle:
                csv.writer(handle).writerow(CSV_FIELDS)

    def write_meta(self, meta):
        """Record the campaign's fixed facts once; a later site of the same campaign keeps them."""
        path = self.dir / "meta.json"
        if not path.exists():
            path.write_text(json.dumps(meta, indent=2) + "\n")

    def write_run(self, record, exchanges, lines):
        where = {name: record[name] for name in RUN_KEY_FIELDS}
        with self._csv.open("a", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            for exchange in exchanges:
                writer.writerow({**where, **exchange})
        with self._serial.open("a") as handle:
            handle.write(f"# {json.dumps(where)} at {record['started_at']}\n")
            handle.write("\n".join(lines) + "\n")
        with self._runs.open("a") as handle:   # last: a run counts as recorded once this is in
            handle.write(json.dumps(record) + "\n")

    def completed(self):
        """(distance_m, round, key) of every run that reached END without an error."""
        return {(r["distance_m"], r["round"], r["key"]) for r in load_runs(self.dir)
                if r["complete"] and not r["error"]}


def load_runs(results_dir):
    path = Path(results_dir) / "runs.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_exchanges(results_dir):
    path = Path(results_dir) / "exchanges.csv"
    if not path.exists():
        return []
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


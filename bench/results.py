"""Streaming results writer.

Rows are appended and flushed after every configuration point rather than held in memory and
written at the end: a ten-hour campaign that loses everything to a USB hiccup in hour nine is not
an acceptable failure mode, and `--resume` needs the partial file to pick up from.
"""

import csv
import json
import time
from pathlib import Path

from .records import NODE_COLUMNS, RAW_COLUMNS, STATUS_NAMES


class ResultWriter:
    def __init__(self, out_dir):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.raw_path = self.out_dir / "raw.csv"
        self.node_path = self.out_dir / "node_rx.csv"
        self.points_path = self.out_dir / "points.jsonl"

        fresh = not self.raw_path.exists()
        self._raw_file = self.raw_path.open("a", newline="")
        self._node_file = self.node_path.open("a", newline="")
        self._points_file = self.points_path.open("a")
        self._raw = csv.writer(self._raw_file)
        self._node = csv.writer(self._node_file)
        if fresh:
            self._raw.writerow(RAW_COLUMNS)
            self._node.writerow(NODE_COLUMNS)

    def close(self):
        for handle in (self._raw_file, self._node_file, self._points_file):
            handle.close()

    def completed_indices(self):
        """Run indices already present, so --resume can skip them."""
        if not self.points_path.exists():
            return set()
        done = set()
        with self.points_path.open() as handle:
            for line in handle:
                try:
                    done.add(json.loads(line)["run_index"])
                except (json.JSONDecodeError, KeyError):
                    continue
        return done

    def write_point(self, run, header, exchanges, summary, node_rx, node_summary, started_at,
                    energy=None):
        point = run.point
        prefix = [run.index, run.rotation, point.tech, point.factor, point.level, point.key]

        for exchange in exchanges:
            self._raw.writerow(prefix + [
                round(started_at, 3), exchange["seq"], exchange["attempt"],
                exchange["payload_bytes"], exchange["total_bytes"],
                exchange["send_us"], exchange["rtt_us"], exchange["status"],
                STATUS_NAMES.get(exchange["status"], exchange["status"]), exchange["acked"],
                "" if exchange["rssi_dbm"] is None else exchange["rssi_dbm"],
                "" if exchange["snr_db"] is None else exchange["snr_db"],
                exchange["duty_pct"], exchange["ok"],
            ])

        for arrival in node_rx:
            self._node.writerow(prefix + [
                arrival["seq"], arrival["payload_bytes"], arrival["total_bytes"],
                "" if arrival["rssi_dbm"] is None else arrival["rssi_dbm"],
                "" if arrival["snr_db"] is None else arrival["snr_db"],
            ])

        # One JSON line per configuration point: the inputs recorded with the outputs, which is
        # what practice P14 (benchmark coverage) needs to be checkable after the fact.
        self._points_file.write(json.dumps({
            "run_index": run.index,
            "rotation": run.rotation,
            "tech": point.tech,
            "factor": point.factor,
            "level": point.level,
            "point_key": point.key,
            "params": point.params,
            "started_at": started_at,
            "finished_at": time.time(),
            "header": header,
            "summary": summary,
            "node_summary": node_summary,
            "energy": energy,
            "exchange_count": len(exchanges),
            "node_rx_count": len(node_rx),
        }) + "\n")

        self._raw_file.flush()
        self._node_file.flush()
        self._points_file.flush()


def write_meta(out_dir, meta):
    Path(out_dir, "meta.json").write_text(json.dumps(meta, indent=2))


def load_points(results_dir):
    path = Path(results_dir) / "points.jsonl"
    if not path.exists():
        return []
    out = []
    with path.open() as handle:
        for line in handle:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def load_raw(results_dir):
    path = Path(results_dir) / "raw.csv"
    if not path.exists():
        return []
    with path.open() as handle:
        return list(csv.DictReader(handle))


def load_node_rx(results_dir):
    path = Path(results_dir) / "node_rx.csv"
    if not path.exists():
        return []
    with path.open() as handle:
        return list(csv.DictReader(handle))


def load_meta(results_dir):
    path = Path(results_dir) / "meta.json"
    return json.loads(path.read_text()) if path.exists() else {}

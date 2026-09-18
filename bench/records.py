"""Parsing the firmware's PP* records, and the raw CSV schema they land in.

Parsing is defensive by design. The Zigbee path interleaves raw XBee API frame bytes into the
serial stream, so a line may contain binary before the record starts and may be truncated after
it; a malformed record is skipped rather than aborting a ten-hour campaign.
"""

STATUS_NAMES = {0: "Ok", 1: "Timeout", 2: "DutyCycleExceeded", 3: "PayloadTooLarge",
                4: "NotReady", 5: "Failed"}

# One row per gateway send attempt, successes and failures alike.
RAW_COLUMNS = [
    "run_index", "rotation", "tech", "factor", "level", "point_key",
    "started_at", "seq", "attempt", "payload_bytes", "total_bytes",
    "send_us", "rtt_us", "status", "status_name", "acked", "rssi_dbm", "snr_db",
    "duty_pct", "ok",
]

# One row per arrival observed at the receiver - the only place PDR can honestly be counted.
NODE_COLUMNS = [
    "run_index", "rotation", "tech", "factor", "level", "point_key",
    "seq", "payload_bytes", "total_bytes", "rssi_dbm", "snr_db",
]


def _optional_float(text):
    return float(text) if text else None


def parse_records(records):
    """Split a list of raw record strings into header / exchanges / summary / node data."""
    header, exchanges, summary, node_rx, node_summary = {}, [], None, [], None

    for record in records:
        fields = record.split(",")
        kind = fields[0]
        try:
            if kind == "PPH" and len(fields) >= 3:
                header[fields[1]] = ",".join(fields[2:]).strip()
            elif kind == "PPD" and len(fields) >= 13:
                exchanges.append({
                    "seq": int(fields[1]),
                    "attempt": int(fields[2]),
                    "payload_bytes": int(fields[3]),
                    "total_bytes": int(fields[4]),
                    "send_us": int(fields[5]),
                    "rtt_us": int(fields[6]),
                    "status": int(fields[7]),
                    "acked": int(fields[8]),
                    "rssi_dbm": _optional_float(fields[9]),
                    "snr_db": _optional_float(fields[10]),
                    "duty_pct": float(fields[11]),
                    "ok": int(fields[12]),
                })
            elif kind == "PPS" and len(fields) >= 8:
                summary = {
                    "successes": int(fields[1]),
                    "attempts": int(fields[2]),
                    "retries": int(fields[3]),
                    "elapsed_ms": int(fields[4]),
                    "duty_pct": float(fields[5]),
                    "send_us_total": int(fields[6]),
                    "duty_blocked": int(fields[7]),
                }
            elif kind == "PPN" and len(fields) >= 6:
                node_rx.append({
                    "seq": int(fields[1]),
                    "payload_bytes": int(fields[2]),
                    "total_bytes": int(fields[3]),
                    "rssi_dbm": _optional_float(fields[4]),
                    "snr_db": _optional_float(fields[5]),
                })
            elif kind == "PPI" and len(fields) >= 4:
                summary = {
                    "idle": True,
                    "tech": fields[1],
                    "elapsed_ms": int(fields[2]),
                    "polls": int(fields[3]),
                    "successes": 0, "attempts": 0, "retries": 0,
                    "duty_pct": 0.0, "send_us_total": 0, "duty_blocked": 0,
                }
            elif kind == "PPM" and len(fields) >= 5:
                node_summary = {
                    "rx_distinct": int(fields[1]),
                    "rx_total": int(fields[2]),
                    "replies_ok": int(fields[3]),
                    "reply_failures": int(fields[4]),
                }
        except (ValueError, IndexError):
            continue   # corrupted by interleaved binary; drop this record only

    return header, exchanges, summary, node_rx, node_summary

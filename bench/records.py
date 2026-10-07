"""Parsing the node's serial records (src/node.cpp) into one result per run.

Parsing is defensive: a malformed line is skipped rather than aborting a day in the field.
"""

# connectionlib::SendStatus
STATUS_NAMES = {0: "Ok", 1: "Timeout", 2: "DutyCycleExceeded", 3: "PayloadTooLarge",
                4: "NotReady", 5: "Failed", 6: "Queued"}

# One row per ping. `rssi_dbm` / `snr_db` are the node's reading of the pong; `gw_rssi_dbm` /
# `gw_snr_db` are the gateway's reading of the ping, carried back in the pong. `gw_rx` is how many
# pings of this run the gateway had received when it sent that pong.
EXCHANGE_FIELDS = ["seq", "payload_bytes", "total_bytes", "send_us", "rtt_us", "status", "acked",
                   "rssi_dbm", "snr_db", "gw_rx", "gw_rssi_dbm", "gw_snr_db", "duty_pct", "ok"]
_FLOAT_FIELDS = {"rssi_dbm", "snr_db", "gw_rssi_dbm", "gw_snr_db", "duty_pct"}
SUMMARY_FIELDS = ["sent", "answered", "gw_rx", "elapsed_ms", "duty_pct", "aborted"]


def _number(name, text):
    if text == "":
        return None
    return float(text) if name in _FLOAT_FIELDS else int(text)


def _fields(names, values):
    if len(values) != len(names):
        raise ValueError("wrong field count")
    return {name: _number(name, value) for name, value in zip(names, values)}


def parse(lines):
    """Split the lines of one run into header, handshake, idle, exchanges and summary."""
    run = {"header": {}, "home": None, "idle_ms": None, "exchanges": [], "summary": None,
           "release": None, "error": None, "complete": False}
    for line in lines:
        kind, _, rest = line.partition(",")
        parts = rest.split(",") if rest else []
        try:
            if kind == "PPH" and len(parts) >= 2:
                run["header"][parts[0]] = ",".join(parts[1:])
            elif kind == "HOME":
                run["home"] = {"acked": parts[0] == "ok"}
                if parts[0] == "ok":
                    run["home"].update(rssi_dbm=_number("rssi_dbm", parts[1]),
                                       snr_db=_number("snr_db", parts[2]), attempts=int(parts[3]))
            elif kind == "IDLE" and parts[0] == "end":
                run["idle_ms"] = int(parts[1])
            elif kind == "PPD":
                exchange = _fields(EXCHANGE_FIELDS, parts)
                exchange["status_name"] = STATUS_NAMES.get(exchange["status"], "?")
                run["exchanges"].append(exchange)
            elif kind == "PPS":
                run["summary"] = _fields(SUMMARY_FIELDS, parts)
            elif kind == "RELEASE":
                run["release"] = parts[0]
            elif kind == "ERR":
                run["error"] = rest
            elif kind == "END":
                run["complete"] = True
        except (ValueError, IndexError):
            continue
    return run


def delivery(run):
    """Packet delivery per direction, counted where the packet arrived.

    Uplink is the gateway's own count of pings, as its pongs (or the final count request) carried
    it back. Downlink is pongs received over pings the gateway answered. If no pong ever arrived
    the gateway's count is unknown, and uplink is reported as None rather than as zero.
    """
    summary = run["summary"]
    if not summary or not summary["sent"]:
        return {"sent": 0, "answered": 0, "uplink_pdr": None, "downlink_pdr": None,
                "round_trip_pdr": None}
    sent, answered, gateway_rx = summary["sent"], summary["answered"], summary["gw_rx"]
    heard = answered > 0 or gateway_rx > 0
    return {
        "sent": sent,
        "answered": answered,
        "uplink_pdr": min(gateway_rx / sent, 1.0) if heard else None,
        "downlink_pdr": min(answered / gateway_rx, 1.0) if gateway_rx else None,
        "round_trip_pdr": answered / sent,
    }

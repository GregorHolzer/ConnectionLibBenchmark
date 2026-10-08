"""Parsing the node's serial records (src/node.cpp) into one result per run.

Parsing is defensive: a malformed line is skipped rather than aborting a day in the field.
"""

# connectionlib::SendStatus
STATUS_NAMES = {0: "Ok", 1: "Timeout", 2: "DutyCycleExceeded", 3: "PayloadTooLarge",
                4: "NotReady", 5: "Failed", 6: "Queued"}

# One row per payload. `latency_us` is the time spent in send(): from handing the payload to the
# library until the library had its ack, or gave up. `acked` is that ack.
EXCHANGE_FIELDS = ["seq", "payload_bytes", "total_bytes", "latency_us", "status", "acked",
                   "duty_pct"]
_FLOAT_FIELDS = {"duty_pct"}
SUMMARY_FIELDS = ["sent", "acked", "elapsed_ms", "duty_pct", "aborted"]


def _number(name, text):
    if text == "":
        return None
    return float(text) if name in _FLOAT_FIELDS else int(text)


def _fields(names, values):
    if len(values) != len(names):
        raise ValueError("wrong field count")
    return {name: _number(name, value) for name, value in zip(names, values)}


def parse(lines):
    """Split the lines of one run into header, handshake, idle, payloads, summary and report."""
    run = {"header": {}, "home": None, "idle_ms": None, "exchanges": [], "summary": None,
           "report": None, "error": None, "complete": False}
    for line in lines:
        kind, _, rest = line.partition(",")
        parts = rest.split(",") if rest else []
        try:
            if kind == "PPH" and len(parts) >= 2:
                run["header"][parts[0]] = ",".join(parts[1:])
            elif kind == "HOME":
                run["home"] = {"acked": parts[0] == "ok",
                               "attempts": int(parts[1]) if parts[0] == "ok" else None}
            elif kind == "IDLE" and parts[0] == "end":
                run["idle_ms"] = int(parts[1])
            elif kind == "PPD":
                exchange = _fields(EXCHANGE_FIELDS, parts)
                exchange["status_name"] = STATUS_NAMES.get(exchange["status"], "?")
                run["exchanges"].append(exchange)
            elif kind == "PPS":
                run["summary"] = _fields(SUMMARY_FIELDS, parts)
            elif kind == "REPORT":
                # "requested": the gateway acked the request, so on LoRa it has gone home.
                run["report"] = {"requested": parts[0] in ("ok", "lost"), "received": None,
                                 "rssi_dbm": None, "snr_db": None}
                if parts[0] == "ok":
                    run["report"].update(received=int(parts[1]),
                                         rssi_dbm=float(parts[2]) if parts[2] else None,
                                         snr_db=float(parts[3]) if parts[3] else None)
            elif kind == "ERR":
                run["error"] = rest
            elif kind == "END":
                run["complete"] = True
        except (ValueError, IndexError):
            continue
    return run


def delivery(run):
    """Delivery of one run, as the library confirmed it and as the gateway counted it.

    `pdr` is acked payloads over sent: what an application using the library would see.
    `gateway_received` is the gateway's own count, from its report; it can exceed `acked`,
    because a payload can arrive while its ack is lost. None when no report came back.
    """
    summary, report = run["summary"], run["report"] or {}
    if not summary or not summary["sent"]:
        return {"sent": 0, "acked": 0, "pdr": None, "gateway_received": None, "gateway_pdr": None}
    received = report.get("received")
    return {
        "sent": summary["sent"],
        "acked": summary["acked"],
        "pdr": summary["acked"] / summary["sent"],
        "gateway_received": received,
        "gateway_pdr": None if received is None else min(received / summary["sent"], 1.0),
    }

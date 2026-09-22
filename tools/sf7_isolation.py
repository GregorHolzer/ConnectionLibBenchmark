#!/usr/bin/env python3
"""Does lora/sf/7 kill the gateway on its own, or only after the ESP-NOW raw points?

Two arms, each starting from a fresh boot:

    cold  - CFG lora sf=7, RUN.                     Nothing else has touched the radios.
    after - the four espnow stack=raw points first, then the same lora sf=7 run.

If `cold` survives and `after` does not, the ESP-NOW raw arm is leaving the board in a state that
the LoRa run cannot survive - a real regression. If both die, sf/7 is the trigger by itself and the
cause is the supply, not the software.
"""
import argparse, sys, time
sys.path.insert(0, "/home/gregor/ConnectionLibBenchmark")
from bench.device import Device, DeviceError

GW = "/dev/serial/by-id/usb-1a86_USB_Single_Serial_573C000618-if00"
ND = "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5748001588-if00"
PEER_GW = "08:F9:E0:D0:38:30"   # node's MAC, what the gateway targets
PEER_ND = "08:F9:E0:D0:43:1C"   # gateway's MAC, what the node targets

LORA_SF7 = {"tech": "lora", "freq": 433.2, "bw": 125, "sf": 7, "cr": 7, "pwr": 7,
            "preamble": 8, "payload": 32, "spacing": 100, "replywait": 2000}


def espnow_raw(payload, peer):
    return {"tech": "espnow", "stack": "raw", "channel": 1, "encrypt": 0, "timeout": 2000,
            "payload": payload, "spacing": 100, "replywait": 2000, "peermac": peer}


def run_point(gw, nd, params_gw, params_nd, exchanges, label):
    nd.configure(params_nd, timeout_s=25)
    gw.configure({**params_gw, "n": exchanges}, timeout_s=25)
    t0 = time.time()
    try:
        records = gw.run(timeout_s=120)
    except Exception as exc:
        print(f"    {label:32s} FAILED after {time.time()-t0:.0f}s: {exc}")
        return False
    ok = sum(1 for r in records if r.startswith("PPD,") and r.rstrip().endswith(",1"))
    print(f"    {label:32s} ok  {ok}/{exchanges} in {time.time()-t0:.0f}s")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["cold", "after"], required=True)
    args = ap.parse_args()

    gw, nd = Device(GW, name="gw"), Device(ND, name="node")
    time.sleep(2.0)
    print(f"  arm = {args.arm}")
    try:
        if args.arm == "after":
            for payload in (8, 16, 32, 84):
                if not run_point(gw, nd, espnow_raw(payload, PEER_GW),
                                  espnow_raw(payload, PEER_ND), 100, f"espnow raw payload={payload}"):
                    return 1
        survived = run_point(gw, nd, LORA_SF7, LORA_SF7, 20, "lora sf=7")
    finally:
        gw.close(); nd.close()
    print(f"\n  {args.arm}: {'SURVIVED' if survived else 'GATEWAY DIED'}")
    return 0 if survived else 1


if __name__ == "__main__":
    sys.exit(main())

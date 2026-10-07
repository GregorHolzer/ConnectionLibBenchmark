"""Host-side checks that need no hardware: `python -m unittest discover tests`."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench import airtime, configs, firmware, records, results, summary   # noqa: E402

# One LoRa run as src/node.cpp prints it: 4 payloads, the third never acked - but the gateway's
# report says all four arrived, so that one was a lost ack, not a lost payload.
LORA_RUN = """PPH,tech,LoRa
PPH,runId,7
HOME,ok,1
PPH,technologyName,LoRa-SX1278
RUN,begin
RUN,end
PPD,0,32,39,541200,0,1,0.0092
PPD,1,32,39,540900,0,1,0.0183
PPD,2,32,39,1438000,1,0,0.0275
PPD,3,32,39,541600,0,1,0.0367
PPS,4,3,3062,0.0367,0
REPORT,ok,4,-99,7.25
END""".splitlines()


class Airtime(unittest.TestCase):
    def test_matches_the_value_validated_on_hardware(self):
        # 197.6 ms predicted at SF9 / 8 B, against 201.9 ms measured (docs/findings.md).
        self.assertAlmostEqual(airtime.time_on_air_ms(8, header_bytes=6), 197.6, delta=0.1)

    def test_low_data_rate_optimisation_applies_from_sf11(self):
        self.assertGreater(airtime.time_on_air_ms(32, sf=12), 2000)

    def test_ledger_survives_a_restart_and_blocks_when_full(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ledger.txt"
            airtime.AirtimeBudget(path).record(300.0)
            budget = airtime.AirtimeBudget(path)
            self.assertAlmostEqual(budget.used(), 300.0, places=1)
            self.assertEqual(budget.wait_for(10.0), 0.0)
            slept = []
            budget.wait_for(100.0, sleeper=lambda s: (slept.append(s), budget._entries.clear()),
                            log=lambda _: None)
            self.assertTrue(slept and slept[0] > 3000)   # until the old entry leaves the hour

    def test_a_clean_point_is_charged_less_than_the_reserved_bound(self):
        clean = airtime.point_airtime_s(330, 200, 20, switch_attempts=1, clean=True)
        self.assertLess(clean, airtime.point_airtime_s(330, 200, 20))


class Configs(unittest.TestCase):
    def test_site_is_one_point_per_24ghz_radio_and_the_lora_sweep(self):
        site = configs.site_configs()
        self.assertEqual([c.key for c in site[:3]],
                         ["zigbee/baseline", "espnow/baseline", "lora/baseline"])
        self.assertEqual(sum(c.tech == "lora" for c in site), 16)
        self.assertEqual(len({c.key for c in site}), len(site))

    def test_reply_wait_and_lease_cover_the_slowest_point(self):
        slow = next(c for c in configs.lora_configs() if c.name == "sf=12")
        ack_ms = airtime.time_on_air_ms(0, sf=12)
        self.assertGreater(configs.ack_wait_ms(slow), ack_ms)
        self.assertLessEqual(configs.lease_seconds(slow), 1020)
        self.assertEqual(configs.lease_seconds(slow) % 4, 0)

    def test_one_site_fits_in_an_hour_of_duty_cycle(self):
        total = sum(airtime.point_airtime_s(configs.payload_airtime_ms(c), 200, c.exchanges, 1, True)
                    for c in configs.lora_configs())
        self.assertLess(total, airtime.DUTY_CYCLE_FRACTION * airtime.DUTY_CYCLE_WINDOW_S)


class Firmware(unittest.TestCase):
    def test_header_carries_the_point(self):
        config = next(c for c in configs.lora_configs() if c.name == "sf=12")
        text = firmware.render_header(config, "AA:BB:CC:00:11:22", "0013A20012345678", 300)
        self.assertIn("#define BENCH_TECH LoRa\n", text)
        self.assertIn("#define BENCH_LORA_SF 12\n", text)
        self.assertIn("#define BENCH_RUN_ID 44\n", text)   # one byte on the wire
        self.assertIn("#define BENCH_PEER_MAC {0xAA, 0xBB, 0xCC, 0x00, 0x11, 0x22}\n", text)
        self.assertIn("#define BENCH_PEER_XBEE 0x0013A20012345678ULL\n", text)


class Records(unittest.TestCase):
    def test_parse_and_delivery(self):
        run = records.parse(LORA_RUN)
        self.assertTrue(run["complete"])
        self.assertEqual(run["home"], {"acked": True, "attempts": 1})
        self.assertEqual(len(run["exchanges"]), 4)
        self.assertEqual(run["exchanges"][2]["status_name"], "Timeout")
        self.assertEqual(run["report"], {"requested": True, "received": 4, "rssi_dbm": -99.0,
                                         "snr_db": 7.25})
        delivery = records.delivery(run)
        self.assertEqual(delivery["pdr"], 0.75)           # what the library confirmed
        self.assertEqual(delivery["gateway_pdr"], 1.0)    # what actually arrived

    def test_garbage_is_skipped_and_a_missing_report_is_not_zero(self):
        run = records.parse(["PPD,not,a,record", "PPS,10,0,172,0.0,1", "REPORT,noack", "END"])
        self.assertEqual(run["exchanges"], [])
        self.assertFalse(run["report"]["requested"])
        self.assertIsNone(records.delivery(run)["gateway_pdr"])
        self.assertEqual(records.delivery(run)["pdr"], 0.0)
        self.assertTrue(records.parse(["REPORT,lost"])["report"]["requested"])


class Summary(unittest.TestCase):
    def test_round_trip_from_disk_to_metrics(self):
        run = records.parse(LORA_RUN)
        energy = {"run": {"duration_s": 3.0, "mean_ua": 90000.0, "energy_mj": 1350.0,
                          "voltage_mv": 5000}}
        standing = {"idle": {"duration_s": 30.0, "mean_ua": 86000.0, "energy_mj": 12900.0,
                             "voltage_mv": 5000}}
        base = {"round": 0, "started_at": "t", "run_id": 7, "header": {}, "home": None,
                "idle_ms": None, "report": None, "payload_airtime_ms": 341.0, "duty_wait_s": 0,
                "error": None, "complete": True}
        with tempfile.TemporaryDirectory() as folder:
            writer = results.ResultWriter(folder)
            writer.write_run({**base, "distance_m": None, "key": "lora/standing", "tech": "lora",
                              "name": "standing", "params": {}, "exchanges_planned": 0,
                              "summary": None, "delivery": {}, "energy": standing}, [], [])
            writer.write_run({**base, "distance_m": 100.0, "key": "lora/baseline", "tech": "lora",
                              "name": "baseline", "params": {"payload": 32},
                              "exchanges_planned": 20, "summary": run["summary"],
                              "home": run["home"], "report": run["report"],
                              "delivery": records.delivery(run), "energy": energy},
                             run["exchanges"], LORA_RUN)
            self.assertEqual(writer.completed(), {(None, 0, "lora/standing"),
                                                  (100.0, 0, "lora/baseline")})
            rows = summary.summarise(results.load_runs(folder), results.load_exchanges(folder))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["sent"], row["acked"]), (4, 3))
        self.assertEqual((row["pdr"], row["gateway_pdr"]), (0.75, 1.0))
        self.assertEqual(row["latency_us"]["median"], 541200)   # acked payloads only
        self.assertAlmostEqual(row["goodput_bps"], 3 * 32 * 8 / 3.062)
        self.assertAlmostEqual(row["framing_efficiency"], 32 / 39)
        self.assertEqual((row["gw_rssi_dbm"], row["gw_snr_db"]), (-99.0, 7.25))
        self.assertAlmostEqual(row["energy"]["energy_per_delivered_mj"], 450.0)
        # 4 mA above standing for 3 s at 5 V = 60 mJ, over three delivered payloads.
        self.assertAlmostEqual(row["energy"]["excess_energy_per_delivered_mj"], 20.0)
        self.assertIn("lora/baseline", summary.table(rows))


if __name__ == "__main__":
    unittest.main()

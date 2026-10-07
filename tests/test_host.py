"""Host-side checks that need no hardware: `python -m unittest discover tests`."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench import airtime, configs, firmware, records, results, summary   # noqa: E402

# One LoRa run as src/node.cpp prints it: 4 pings, the third lost on the way back.
LORA_RUN = """PPH,tech,LoRa
PPH,runId,7
HOME,ok,-101.0,6.5,1
PPH,technologyName,LoRa-SX1278
RUN,begin
PPD,0,32,38,330100,662300,0,0,-98.0,7.2,1,-99,7.0,0.0092,1
PPD,1,32,38,330200,662900,0,0,-98.5,7.0,2,-99,6.8,0.0183,1
PPD,2,32,38,330100,0,0,0,,,2,,,0.0275,0
PPD,3,32,38,330300,663100,0,0,-97.0,7.5,4,-98,7.2,0.0367,1
PPS,4,3,4,4321,0.0367,0
RELEASE,ok
END""".splitlines()


class Airtime(unittest.TestCase):
    def test_matches_the_value_validated_on_hardware(self):
        # 197.6 ms predicted at SF9 / 8 B, against 201.9 ms measured (docs/findings.md).
        self.assertAlmostEqual(airtime.time_on_air_ms(8), 197.6, delta=0.1)

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
        self.assertGreater(configs.reply_wait_ms(slow), configs.ping_airtime_ms(slow))
        self.assertLessEqual(configs.lease_seconds(slow), 1020)
        self.assertEqual(configs.lease_seconds(slow) % 4, 0)

    def test_one_site_fits_in_an_hour_of_duty_cycle(self):
        total = sum(airtime.point_airtime_s(configs.ping_airtime_ms(c), 200, c.exchanges, 1, True)
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
    def test_parse_and_delivery_per_direction(self):
        run = records.parse(LORA_RUN)
        self.assertTrue(run["complete"])
        self.assertEqual(run["home"], {"acked": True, "rssi_dbm": -101.0, "snr_db": 6.5,
                                       "attempts": 1})
        self.assertEqual(len(run["exchanges"]), 4)
        self.assertIsNone(run["exchanges"][2]["rssi_dbm"])
        delivery = records.delivery(run)
        self.assertEqual(delivery["uplink_pdr"], 1.0)      # the gateway heard all four
        self.assertEqual(delivery["downlink_pdr"], 0.75)   # one pong was lost
        self.assertEqual(delivery["round_trip_pdr"], 0.75)

    def test_garbage_is_skipped_and_silence_is_not_zero_uplink(self):
        run = records.parse(["PPD,not,a,record", "PPS,10,0,0,5000,0.0,1", "END"])
        self.assertEqual(run["exchanges"], [])
        self.assertIsNone(records.delivery(run)["uplink_pdr"])
        self.assertEqual(records.delivery(run)["round_trip_pdr"], 0.0)


class Summary(unittest.TestCase):
    def test_round_trip_from_disk_to_metrics(self):
        run = records.parse(LORA_RUN)
        energy = {"run": {"duration_s": 4.3, "mean_ua": 90000.0, "energy_mj": 1935.0,
                          "voltage_mv": 5000}}
        standing = {"idle": {"duration_s": 30.0, "mean_ua": 86000.0, "energy_mj": 12900.0,
                             "voltage_mv": 5000}}
        base = {"round": 0, "started_at": "t", "run_id": 7, "header": {}, "home": None,
                "idle_ms": None, "release": None, "ping_airtime_ms": 330.0, "duty_wait_s": 0,
                "error": None, "complete": True}
        with tempfile.TemporaryDirectory() as folder:
            writer = results.ResultWriter(folder)
            writer.write_run({**base, "distance_m": None, "key": "lora/standing", "tech": "lora",
                              "name": "standing", "params": {}, "exchanges_planned": 0,
                              "summary": None, "delivery": {}, "energy": standing}, [], [])
            writer.write_run({**base, "distance_m": 100.0, "key": "lora/baseline", "tech": "lora",
                              "name": "baseline", "params": {"payload": 32},
                              "exchanges_planned": 20, "summary": run["summary"],
                              "home": run["home"], "delivery": records.delivery(run),
                              "energy": energy}, run["exchanges"], LORA_RUN)
            self.assertEqual(writer.completed(), {(None, 0, "lora/standing"),
                                                  (100.0, 0, "lora/baseline")})
            rows = summary.summarise(results.load_runs(folder), results.load_exchanges(folder))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["sent"], row["answered"]), (4, 3))
        self.assertEqual(row["downlink_pdr"], 0.75)
        self.assertEqual(row["rtt_us"]["median"], 662900)
        self.assertAlmostEqual(row["goodput_bps"], 3 * 32 * 8 / 4.321)
        self.assertAlmostEqual(row["framing_efficiency"], 32 / 38)
        self.assertAlmostEqual(row["energy"]["energy_per_delivered_mj"], 645.0)
        # 4 mA above standing for 4.3 s at 5 V = 86 mJ, over three delivered payloads.
        self.assertAlmostEqual(row["energy"]["excess_energy_per_delivered_mj"], 86.0 / 3)
        self.assertIn("lora/baseline", summary.table(rows))


if __name__ == "__main__":
    unittest.main()

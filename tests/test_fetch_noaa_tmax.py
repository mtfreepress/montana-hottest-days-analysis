import csv
import gzip
import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "fetch_noaa_tmax.py"
SPEC = importlib.util.spec_from_file_location("fetch_noaa_tmax", MODULE_PATH)
fetch = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
import sys
sys.modules[SPEC.name] = fetch
SPEC.loader.exec_module(fetch)


class FetchNoaaTmaxTest(unittest.TestCase):
    def test_station_conversion_qc_and_ties(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archives = root / "archives"
            station_csvs = root / "tmax"
            archives.mkdir()
            stations = [
                fetch.Station("USC00000001", "ONE", "45", "-110", "1000"),
                fetch.Station("USC00000002", "TWO", "46", "-111", "1100"),
            ]
            raw = {
                "USC00000001": [
                    ["USC00000001", "20260712", "TMAX", "400", "", "", "0", "1800"],
                    ["USC00000001", "20260713", "TMAX", "500", "", "X", "0", "1800"],
                    ["USC00000001", "20260712", "PRCP", "5", "", "", "0", "1800"],
                ],
                "USC00000002": [
                    ["USC00000002", "20260712", "TMAX", "400", "", "", "0", ""],
                    ["USC00000002", "20260713", "TMAX", "410", "", "", "0", ""],
                ],
            }
            for station in stations:
                archive = archives / f"{station.station_id}.csv.gz"
                with gzip.open(archive, "wt", newline="", encoding="ascii") as out:
                    csv.writer(out).writerows(raw[station.station_id])
                fetch.write_station_tmax(archive, station_csvs / f"{station.station_id}.csv", station)

            fetch.summarize(stations, station_csvs, root, {7})
            with (root / "montana_statewide_daily_tmax.csv").open(newline="") as source:
                rows = list(csv.DictReader(source))

            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["tmax_f"], "104.0")
            self.assertEqual(rows[0]["winning_station_count"], "2")
            self.assertEqual(rows[1]["station_ids"], "USC00000002")

    def test_month_parser(self):
        self.assertEqual(fetch.parse_months("3-10"), set(range(3, 11)))
        self.assertEqual(fetch.parse_months("4,7,10"), {4, 7, 10})


if __name__ == "__main__":
    unittest.main()

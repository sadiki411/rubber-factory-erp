"""Synthetic CSV tool checks, not phone measurements."""
import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from flowcards_pilot_field_metrics import FIELDS

SCRIPT = Path(__file__).with_name("flowcards_pilot_field_metrics.py")


class FieldMetricsTests(unittest.TestCase):
    def run_tool(self, rows):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "synthetic.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=FIELDS)
                writer.writeheader(); writer.writerows(rows)
            return subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True, encoding="utf-8")

    def good(self, trial, duration, **extra):
        return {"device": "SYNTH-PHONE", "app_version": "SYNTH-1", "code_commit": "SYNTH-COMMIT",
                "network": "SYNTH-WIFI", "operation": "scan_ready", "card_class": "ordinary", "trial": trial,
                "start_ms": 100, "end_ms": 100 + duration, "status": "OK", **extra}

    def test_blank_is_untested_not_zero(self):
        result = self.run_tool([{"operation": "scan_ready", "card_class": "ordinary", "trial": 1}])
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)["reports"][0]
        self.assertEqual(row["unmeasured"], 1)
        self.assertIsNone(row["p50_ms"]); self.assertIsNone(row["scan_over_2000_ms"])
        self.assertEqual(row["measurement_status"], "INCOMPLETE")

    def test_percentiles_failure_and_missing_remain_separate(self):
        rows = [self.good(i, i * 100) for i in range(1, 21)]
        result = self.run_tool(rows)
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)["reports"][0]
        self.assertEqual((row["p50_ms"], row["p95_ms"]), (1050, 1900))
        self.assertEqual(row["measurement_status"], "COLLECTED")
        result = self.run_tool([self.good(1, 500), {**self.good(2, 0), "status": "FAIL", "note": "合成失败"},
                                {"operation": "scan_ready", "card_class": "ordinary", "trial": 3}])
        rows = json.loads(result.stdout)["reports"]
        self.assertEqual(sum(r["failed"] for r in rows), 1)
        self.assertEqual(sum(r["unmeasured"] for r in rows), 1)

    def test_bad_or_duplicate_data_is_rejected(self):
        for rows in ([self.good(1, -1)], [self.good(1, 5), self.good(1, 6)], [self.good(1, 5, device="")]):
            self.assertNotEqual(self.run_tool(rows).returncode, 0)

    def test_different_app_versions_are_not_merged(self):
        result = self.run_tool([self.good(1, 5), self.good(1, 6, app_version="SYNTH-2")])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)["reports"]), 2)


if __name__ == "__main__":
    unittest.main()

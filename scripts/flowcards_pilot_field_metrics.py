"""Offline field evidence tool. Creates blank synthetic-only templates or reads CSV.

No requests, credentials, database connections, or business writes. Empty values
stay untested. Successful duration percentiles never hide failed/unmeasured runs.
"""
import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

FIELDS = ["device", "app_version", "code_commit", "network", "operation", "card_class", "trial",
          "start_ms", "end_ms", "status", "note"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", nargs="?", type=Path)
    parser.add_argument("--template", type=Path)
    args = parser.parse_args()
    if args.template:
        args.template.parent.mkdir(parents=True, exist_ok=True)
        with args.template.open("x", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            for card_class in ("ordinary", "history_heavy"):
                for trial in range(1, 21):
                    writer.writerow({"operation": "scan_ready", "card_class": card_class, "trial": trial})
            for operation in ("ship_save", "return_save", "save_retry"):
                writer.writerow({"operation": operation, "card_class": "synthetic", "trial": 1})
        print(json.dumps({"template": str(args.template), "measurements": 0, "status": "UNTESTED"}))
        return
    if not args.csv_path:
        parser.error("Pass a CSV or --template NEW_PATH")
    groups = defaultdict(list)
    with args.csv_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != FIELDS:
            raise ValueError("CSV columns do not match the supplied field template")
        for number, row in enumerate(reader, 2):
            if row["status"] not in ("", "OK", "FAIL"):
                raise ValueError(f"line {number}: status must be empty, OK or FAIL")
            duration = None
            if row["status"] == "OK":
                if not all(row[f] for f in ("device", "app_version", "code_commit", "network", "start_ms", "end_ms")):
                    raise ValueError(f"line {number}: OK requires environment and two timestamps")
                start, end = float(row["start_ms"]), float(row["end_ms"])
                if not math.isfinite(start) or not math.isfinite(end) or end < start:
                    raise ValueError(f"line {number}: invalid timestamps")
                duration = end - start
            elif row["status"] == "FAIL" and not row["note"]:
                raise ValueError(f"line {number}: FAIL requires an explanation")
            groups[(row["operation"], row["card_class"], row["device"], row["network"], row["app_version"], row["code_commit"])].append((row, duration))
    reports = []
    for key, values in groups.items():
        good = sorted(duration for row, duration in values if row["status"] == "OK")
        failed = sum(row["status"] == "FAIL" for row, _ in values)
        missing = sum(not row["status"] for row, _ in values)
        trials = [row["trial"] for row, _ in values if row["status"]]
        if len(trials) != len(set(trials)):
            raise ValueError(f"duplicate measured trial IDs: {key}")
        required = 20 if key[0] == "scan_ready" else 1
        reports.append({"operation": key[0], "card_class": key[1], "device": key[2], "network": key[3],
            "app_version": key[4], "code_commit": key[5],
            "successful": len(good), "failed": failed, "unmeasured": missing, "required_minimum": required,
            "measurement_status": "COLLECTED" if len(good) + failed >= required and not missing else "INCOMPLETE",
            "p50_ms": statistics.median(good) if good else None,
            "p95_ms": good[math.ceil(len(good) * .95) - 1] if good else None,
            "scan_over_2000_ms": sum(t > 2000 for t in good) if key[0] == "scan_ready" and good else None})
    print(json.dumps({"source": str(args.csv_path), "reports": reports,
        "note": "COLLECTED is evidence count only, not acceptance; failures stay visible, blanks are not zero."}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""Reproduce data-quality, time-split validation and policy economics.

Usage: python analyze.py
Only Python's standard library is required. All output comes from the supplied
device_commands.csv. The recovery-savings calculation is a counterfactual upper
bound, not an observed outcome.
"""

import csv
import json
import math
from collections import Counter
from datetime import datetime
from pathlib import Path

from policy import heartbeat_age_minutes


ROOT = Path(__file__).resolve().parent
INPUT = ROOT / "device_commands.csv"
OUTPUT = ROOT / "results.json"
CANDIDATE_THRESHOLDS = (5, 10, 15, 20, 30, 45, 60)
FAILURE_COST_MINUTES = 90
PROBE_COST_MINUTES = 12


def count_status(rows):
    counts = Counter(row["status"] for row in rows)
    return {
        "commands": len(rows),
        "success": counts[1],
        "failure": counts[0],
        "unknown": counts[None],
        "observed_failure_rate": counts[0] / (counts[0] + counts[1]) if counts[0] + counts[1] else None,
    }


def wilson_interval(failures, known, z=1.96):
    if known == 0:
        return None
    p = failures / known
    denominator = 1 + z * z / known
    center = (p + z * z / (2 * known)) / denominator
    width = z * math.sqrt(p * (1 - p) / known + z * z / (4 * known * known)) / denominator
    return [center - width, center + width]


def split_at_age(rows, threshold):
    high = [row for row in rows if row["age_minutes"] >= threshold]
    low = [row for row in rows if row["age_minutes"] < threshold]
    return high, low


def evaluate(rows, threshold):
    high, low = split_at_age(rows, threshold)
    high_counts, low_counts = count_status(high), count_status(low)
    high_known = high_counts["failure"] + high_counts["success"]
    low_known = low_counts["failure"] + low_counts["success"]
    high_counts["failure_rate_wilson_95pct"] = wilson_interval(high_counts["failure"], high_known)
    low_counts["failure_rate_wilson_95pct"] = wilson_interval(low_counts["failure"], low_known)
    return {
        "threshold_minutes": threshold,
        "probe": high_counts,
        "no_probe": low_counts,
        "failure_capture_rate_among_known": (
            high_counts["failure"] / (high_counts["failure"] + low_counts["failure"])
            if high_counts["failure"] + low_counts["failure"] else None
        ),
        "optimistic_net_minutes_if_every_flagged_failure_prevented": (
            high_counts["failure"] * FAILURE_COST_MINUTES - high_counts["commands"] * PROBE_COST_MINUTES
        ),
        "minimum_prevention_fraction_to_break_even": (
            high_counts["commands"] * PROBE_COST_MINUTES /
            (high_counts["failure"] * FAILURE_COST_MINUTES)
            if high_counts["failure"] else None
        ),
    }


def load():
    raw = []
    with INPUT.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "command_id", "dispatched_at", "last_heartbeat_at", "room_name",
            "device_category", "signal_strength", "daily_invocation_count", "execution_status",
        }
        if set(reader.fieldnames or []) != required:
            raise ValueError("Unexpected CSV columns")
        raw = list(reader)

    unique = {}
    original_by_id = {}
    duplicate_rows = 0
    for row in raw:
        key = row["command_id"]
        if key in original_by_id:
            if row != original_by_id[key]:
                raise ValueError("Conflicting rows for command_id " + key)
            duplicate_rows += 1
            continue
        original_by_id[key] = row.copy()
        row = row.copy()
        if row["execution_status"] not in ("", "0", "1"):
            raise ValueError("Invalid execution_status for command_id " + key)
        row["status"] = None if row["execution_status"] == "" else int(row["execution_status"])
        row["age_minutes"] = heartbeat_age_minutes(row["dispatched_at"], row["last_heartbeat_at"])
        row["dispatched"] = datetime.fromisoformat(row["dispatched_at"].replace("Z", "+00:00"))
        row["date_local"] = row["dispatched"].date().isoformat()
        row["hour_local"] = row["dispatched"].hour
        row["signal"] = float(row["signal_strength"])
        row["daily_count"] = int(row["daily_invocation_count"])
        unique[key] = row
    return raw, list(unique.values()), duplicate_rows


def main():
    raw, rows, duplicate_rows = load()
    dates = sorted({row["date_local"] for row in rows})
    if len(dates) < 3:
        raise ValueError("Need at least three dates for a temporal holdout")
    train_dates, holdout_dates = dates[:-2], dates[-2:]
    train = [row for row in rows if row["date_local"] in train_dates]
    holdout = [row for row in rows if row["date_local"] in holdout_dates]
    candidate_results = [evaluate(train, threshold) for threshold in CANDIDATE_THRESHOLDS]
    selected = max(
        candidate_results,
        key=lambda result: (result["optimistic_net_minutes_if_every_flagged_failure_prevented"],
                            -result["threshold_minutes"]),
    )["threshold_minutes"]
    all_eval = evaluate(rows, selected)
    train_eval = evaluate(train, selected)
    holdout_eval = evaluate(holdout, selected)
    by_day = {date: count_status([row for row in rows if row["date_local"] == date]) for date in dates}
    weak_signal = count_status([row for row in rows if row["signal"] < 10])
    stronger_signal = count_status([row for row in rows if row["signal"] >= 10])
    night = [row for row in rows if row["hour_local"] >= 22 or row["hour_local"] < 7]
    result = {
        "source_rows": len(raw),
        "exact_duplicate_rows_removed": duplicate_rows,
        "unique_commands": len(rows),
        "overall": count_status(rows),
        "dates_local": dates,
        "local_hour_min": min(row["hour_local"] for row in rows),
        "local_hour_max": max(row["hour_local"] for row in rows),
        "night_commands_22_to_06_local": len(night),
        "train_dates": train_dates,
        "holdout_dates": holdout_dates,
        "candidate_train_results": candidate_results,
        "selected_threshold_minutes": selected,
        "all": all_eval,
        "train": train_eval,
        "holdout": holdout_eval,
        "by_day": by_day,
        "signal_less_than_10": weak_signal,
        "signal_at_least_10": stronger_signal,
        "failure_cost_minutes": FAILURE_COST_MINUTES,
        "probe_cost_minutes": PROBE_COST_MINUTES,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("raw=%d unique=%d duplicate=%d" % (len(raw), len(rows), duplicate_rows))
    print("overall", count_status(rows))
    print("train dates", train_dates, "holdout dates", holdout_dates)
    print("selected threshold", selected)
    print("train", train_eval)
    print("holdout", holdout_eval)
    print("night 22:00-06:59", len(night))
    print("results saved to", OUTPUT)


if __name__ == "__main__":
    main()

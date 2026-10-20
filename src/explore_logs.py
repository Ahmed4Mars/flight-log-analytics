"""Explore ArduPilot DataFlash (.BIN) logs before designing the schema.

For each log: message types, row counts, fields, approximate rate (Hz),
log duration, armed time and flight-mode changes. Also prints a cross-log
summary so we can decide which message types are worth loading.

Usage:
    python src/explore_logs.py                 # all .BIN files in data/raw
    python src/explore_logs.py path/to/log.BIN # one file or a folder
    python src/explore_logs.py --fields        # also print field lists
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd
from pymavlink import DFReader, mavutil

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW = REPO_ROOT / "data" / "raw"
SUMMARY_CSV = REPO_ROOT / "data" / "processed" / "explore_summary.csv"

# EV ids from ArduPilot AP_Logger LogEvent
EV_ARMED, EV_DISARMED = 10, 11

# Columns that tell sensor instances apart (IMU 0/1/2, GPS 0/1, ...)
INSTANCE_COLS = ("I", "Inst", "Instance", "IMU", "C")
RATE_MIN_ROWS = 50    # below this, a "rate" is meaningless (one-off messages)
RATE_SAMPLE = 500     # messages used to estimate the rate
MIN_PERIOD_US = 1000  # faster than 1 kHz = a burst, not a logging rate


def find_logs(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return sorted(p for p in target.iterdir() if p.suffix.lower() == ".bin")


def parse_at(log: DFReader.DFReader_binary, offset: int):
    """Parse the single message that starts at a byte offset."""
    log.offset = offset
    log.remaining = log.data_len - offset
    return log._parse_next()


def sample_rate(log, offsets: list[int], instance_col: str | None):
    """Logging rate per instance (Hz), from the median gap between samples.

    rows / (last - first) would be wrong: logs often pause while disarmed.
    """
    if len(offsets) < RATE_MIN_ROWS:
        return None, None
    # sample from the middle of the log, away from boot-time bursts
    mid = len(offsets) // 2
    sample = offsets[mid:mid + RATE_SAMPLE]
    msgs = [m for m in (parse_at(log, ofs) for ofs in sample) if m is not None]
    df = pd.DataFrame({
        "t": [m.TimeUS for m in msgs],
        "inst": [getattr(m, instance_col) if instance_col else 0 for m in msgs],
    })
    # instances share the same TimeUS, so take the gap within each instance
    dt_us = df.groupby("inst")["t"].diff().median()
    n_inst = df["inst"].nunique()
    # gaps of ~0 mean a burst (e.g. PARM dumped at boot), not a periodic rate
    if not dt_us or dt_us < MIN_PERIOD_US:
        return None, n_inst
    return round(1e6 / dt_us, 1), n_inst


def message_types(log: DFReader.DFReader_binary) -> pd.DataFrame:
    """One row per message type: count, fields, instances, rate, first/last TimeUS."""
    rows = []
    for type_id, count in enumerate(log.counts):
        if count <= 0 or type_id not in log.formats:
            continue
        fmt = log.formats[type_id]
        first_us = last_us = rate_hz = n_inst = None
        if "TimeUS" in fmt.columns:
            offsets = log.offsets[type_id]
            first_us = parse_at(log, offsets[0]).TimeUS
            last_us = parse_at(log, offsets[-1]).TimeUS
            inst_col = next((c for c in INSTANCE_COLS if c in fmt.columns), None)
            rate_hz, n_inst = sample_rate(log, offsets, inst_col)
        rows.append({
            "msg_type": fmt.name,
            "rows": count,
            "instances": n_inst,
            "rate_hz": rate_hz,
            "n_fields": len(fmt.columns),
            "fields": ",".join(fmt.columns),
            "first_us": first_us,
            "last_us": last_us,
        })
    df = pd.DataFrame(rows).astype({"instances": "Int64"})
    return df.sort_values("rows", ascending=False).reset_index(drop=True)


def flight_events(path: Path) -> dict:
    """Armed time, mode changes, firmware string, error count."""
    log = mavutil.mavlink_connection(str(path))
    armed_at, armed_s = None, 0.0
    modes, firmware, n_err, t = [], None, 0, 0
    while True:
        m = log.recv_match(type=["EV", "MODE", "MSG", "ERR"])
        if m is None:
            break
        t = m.TimeUS
        name = m.get_type()
        if name == "EV":
            if m.Id == EV_ARMED:
                armed_at = t
            elif m.Id == EV_DISARMED and armed_at is not None:
                armed_s += (t - armed_at) / 1e6
                armed_at = None
        elif name == "MODE":
            modes.append(log.flightmode)  # DFReader maps the mode number to a name
        elif name == "MSG" and firmware is None and m.Message.startswith("Ardu"):
            firmware = m.Message
        elif name == "ERR":
            n_err += 1
    if armed_at is not None:  # log ended while still armed
        armed_s += (t - armed_at) / 1e6
    return {"armed_s": round(armed_s, 1), "modes": modes,
            "firmware": firmware, "n_err": n_err}


def explore(path: Path, show_fields: bool) -> pd.DataFrame:
    log = DFReader.DFReader_binary(str(path))
    df = message_types(log)
    events = flight_events(path)

    span_us = df["last_us"].max() - df["first_us"].min()
    print(f"\n=== {path.name}  ({path.stat().st_size / 1e6:.1f} MB)")
    print(f"firmware : {events['firmware']}")
    print(f"duration : {span_us / 1e6 / 60:.1f} min logged, "
          f"{events['armed_s'] / 60:.1f} min armed")
    # MODE is re-logged at each log start, so drop consecutive repeats
    modes = [m for i, m in enumerate(events["modes"]) if i == 0 or m != events["modes"][i - 1]]
    print(f"modes    : {' -> '.join(modes) or '(none)'}")
    print(f"ERR msgs : {events['n_err']}")
    cols = ["msg_type", "rows", "instances", "rate_hz", "n_fields"] + (["fields"] if show_fields else [])
    print(df[cols].to_string(index=False))

    df.insert(0, "log_file", path.name)
    df["log_minutes"] = round(span_us / 1e6 / 60, 1)
    df["armed_minutes"] = round(events["armed_s"] / 60, 1)
    return df


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("target", nargs="?", type=Path, default=DEFAULT_RAW)
    ap.add_argument("--fields", action="store_true", help="print field lists")
    args = ap.parse_args()

    logs = find_logs(args.target)
    if not logs:
        print(f"No .BIN files found in {args.target}")
        return 1

    all_types = pd.concat([explore(p, args.fields) for p in logs], ignore_index=True)

    # Cross-log summary: in how many logs does each type appear, how big is it?
    summary = (all_types.groupby("msg_type")
               .agg(logs=("log_file", "nunique"),
                    total_rows=("rows", "sum"),
                    instances=("instances", "max"),
                    median_hz=("rate_hz", "median"),
                    fields=("fields", "first"))
               .sort_values("total_rows", ascending=False))
    print(f"\n=== SUMMARY across {len(logs)} logs")
    print(summary[["logs", "total_rows", "instances", "median_hz"]].to_string())

    SUMMARY_CSV.parent.mkdir(parents=True, exist_ok=True)
    all_types.to_csv(SUMMARY_CSV, index=False)
    print(f"\nPer-log details written to {SUMMARY_CSV.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

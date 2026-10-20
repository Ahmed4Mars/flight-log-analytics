"""Parse ArduPilot .BIN logs into one CSV per database table.

Output, per log:  data/processed/<log stem>_<sha1[:8]>/
    flight.json        flights row (file name, sha1, firmware, start_utc, ...)
    gps.csv, battery.csv, attitude.csv, altitude.csv, vibration.csv,
    params.csv, modes.csv, events.csv, errors.csv, messages.csv

CSV headers are the MySQL column names (without flight_id, which the
loader assigns). A log whose output folder already exists with the same
sha1 and --every is skipped, so re-running is cheap.

Usage:
    python src/parse_logs.py                  # all logs in data/raw
    python src/parse_logs.py data/raw/00000089.BIN
    python src/parse_logs.py --every 5        # keep every 5th sample of 10 Hz tables (-> 2 Hz)
    python src/parse_logs.py --force          # re-parse even if output exists
"""

import argparse
import csv
import hashlib
import json
import math
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pymavlink import mavutil

REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = REPO_ROOT / "data" / "raw"
OUT_DIR = REPO_ROOT / "data" / "processed"
PARSER_VERSION = 1

# table -> (message type, instance field or None, [(column, message field), ...])
# Column order here = CSV header order = INSERT column order.
TABLES = {
    "gps": ("GPS", "I", [
        ("time_us", "TimeUS"), ("instance", "I"), ("status", "Status"),
        ("num_sats", "NSats"), ("hdop", "HDop"), ("lat", "Lat"), ("lng", "Lng"),
        ("alt_m", "Alt"), ("speed_ms", "Spd"), ("course_deg", "GCrs"), ("vz_ms", "VZ"),
    ]),
    "battery": ("BAT", "Inst", [
        ("time_us", "TimeUS"), ("instance", "Inst"), ("volt", "Volt"),
        ("volt_rest", "VoltR"), ("curr_a", "Curr"), ("used_mah", "CurrTot"),
        ("used_wh", "EnrgTot"), ("temp_c", "Temp"), ("rem_pct", "RemPct"),
    ]),
    "attitude": ("ATT", None, [
        ("time_us", "TimeUS"), ("des_roll", "DesRoll"), ("roll", "Roll"),
        ("des_pitch", "DesPitch"), ("pitch", "Pitch"), ("des_yaw", "DesYaw"), ("yaw", "Yaw"),
    ]),
    "altitude": ("CTUN", None, [
        ("time_us", "TimeUS"), ("thr_in", "ThI"), ("thr_out", "ThO"), ("thr_hover", "ThH"),
        ("des_alt", "DAlt"), ("alt", "Alt"), ("baro_alt", "BAlt"), ("rf_alt", "SAlt"),
        ("des_climb_rate", "DCRt"), ("climb_rate", "CRt"),
    ]),
    "vibration": ("VIBE", "IMU", [
        ("time_us", "TimeUS"), ("imu", "IMU"), ("vibe_x", "VibeX"), ("vibe_y", "VibeY"),
        ("vibe_z", "VibeZ"), ("clip", "Clip"),
    ]),
    "params": ("PARM", None, [
        ("name", "Name"), ("time_us", "TimeUS"), ("value", "Value"), ("default_value", "Default"),
    ]),
    "modes": ("MODE", None, [
        ("time_us", "TimeUS"), ("mode_num", "Mode"), ("reason", "Rsn"),
    ]),
    "events": ("EV", None, [("time_us", "TimeUS"), ("event_id", "Id")]),
    "errors": ("ERR", None, [("time_us", "TimeUS"), ("subsys", "Subsys"), ("ecode", "ECode")]),
    "messages": ("MSG", None, [("time_us", "TimeUS"), ("message", "Message")]),
}
# High-rate tables that --every applies to; events and params are never thinned
DOWNSAMPLED = {"gps", "battery", "attitude", "altitude", "vibration"}
MSG_TO_TABLE = {msg: table for table, (msg, _, _) in TABLES.items()}

GPS_EPOCH = datetime(1980, 1, 6, tzinfo=timezone.utc)
GPS_UTC_LEAP_S = 18   # GPS time runs 18 s ahead of UTC (since 2017)
GPS_FIX_3D = 3


def sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def clean(v):
    """CSV-safe value: NaN/inf become empty (NULL in MySQL), text loses NUL bytes."""
    if isinstance(v, float) and not math.isfinite(v):
        return ""
    if isinstance(v, str):
        return v.replace("\x00", "").strip()
    return v


def boot_utc(m) -> datetime | None:
    """UTC time at boot (time_us = 0), from a GPS message with a 3D fix."""
    if m.Status < GPS_FIX_3D or m.GWk == 0:
        return None
    gps_time = GPS_EPOCH + timedelta(weeks=m.GWk, milliseconds=m.GMS)
    return gps_time - timedelta(seconds=GPS_UTC_LEAP_S, microseconds=m.TimeUS)


def parse_log(path: Path, every: int, force: bool) -> str:
    sha1 = sha1_of(path)
    out = OUT_DIR / f"{path.stem}_{sha1[:8]}"
    manifest = out / "flight.json"
    if manifest.exists() and not force:
        old = json.loads(manifest.read_text())
        if old.get("every") == every and old.get("parser_version") == PARSER_VERSION:
            return f"skip  {path.name} (already parsed)"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    files = {t: (out / f"{t}.csv").open("w", newline="", encoding="utf-8") for t in TABLES}
    writers = {t: csv.writer(f) for t, f in files.items()}
    for table, (_, _, cols) in TABLES.items():
        writers[table].writerow([c for c, _ in cols])

    counts = {t: 0 for t in TABLES}
    seen = {}            # (table, instance) -> samples seen, for --every
    firmware = start_utc = None
    t0 = time.perf_counter()

    log = mavutil.mavlink_connection(str(path))
    try:
        while (m := log.recv_match(type=list(MSG_TO_TABLE))) is not None:
            msg_type = m.get_type()
            table = MSG_TO_TABLE[msg_type]
            _, inst_field, cols = TABLES[table]

            if msg_type == "GPS" and start_utc is None:
                start_utc = boot_utc(m)
            if msg_type == "MSG" and firmware is None and m.Message.startswith("Ardu"):
                firmware = m.Message.strip()

            if every > 1 and table in DOWNSAMPLED:
                key = (table, getattr(m, inst_field) if inst_field else 0)
                n = seen.get(key, 0)
                seen[key] = n + 1
                if n % every:
                    continue

            writers[table].writerow([clean(getattr(m, field, None)) for _, field in cols])
            counts[table] += 1
    finally:
        for f in files.values():
            f.close()

    manifest.write_text(json.dumps({
        "log_file": path.name,
        "file_sha1": sha1,
        "file_mb": round(path.stat().st_size / 1e6, 1),
        "firmware": firmware,
        "start_utc": start_utc.strftime("%Y-%m-%d %H:%M:%S") if start_utc else None,
        "every": every,
        "parser_version": PARSER_VERSION,
        "rows": counts,
    }, indent=2))
    return (f"ok    {path.name}  {time.perf_counter() - t0:5.1f}s  "
            + " ".join(f"{t}={n}" for t, n in counts.items() if n))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("target", nargs="?", type=Path, default=RAW_DIR)
    ap.add_argument("--every", type=int, default=1,
                    help="keep every Nth sample of high-rate tables (default 1 = all)")
    ap.add_argument("--force", action="store_true", help="re-parse even if output exists")
    args = ap.parse_args()
    if args.every < 1:
        ap.error("--every must be >= 1")

    logs = [args.target] if args.target.is_file() else sorted(
        p for p in args.target.iterdir() if p.suffix.lower() == ".bin")
    failed = []
    for p in logs:
        try:
            print(parse_log(p, args.every, args.force), flush=True)
        except Exception as ex:  # one bad log should not stop the batch
            print(f"FAIL  {p.name}: {type(ex).__name__}: {ex}", flush=True)
            failed.append(p.name)
    print(f"\n{len(logs) - len(failed)}/{len(logs)} logs parsed into {OUT_DIR.relative_to(REPO_ROOT)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

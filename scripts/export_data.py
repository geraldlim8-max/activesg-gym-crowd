#!/usr/bin/env python3
"""Export SQLite occupancy.db → data/gyms.json + data/days/YYYY-MM-DD.csv."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "occupancy.db"
DATA_DIR = ROOT / "data"
DAYS_DIR = DATA_DIR / "days"


def export(db_path: Path = DB_PATH, data_dir: Path = DATA_DIR) -> dict:
    days_dir = data_dir / "days"
    days_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        gyms = [
            dict(r)
            for r in conn.execute(
                "SELECT id, name, first_seen, last_seen FROM gyms ORDER BY name"
            )
        ]
        snaps = list(
            conn.execute(
                """
                SELECT gym_id, bucket_ts, capacity_pct, is_closed, scraped_at
                FROM snapshots ORDER BY bucket_ts, gym_id
                """
            )
        )
    finally:
        conn.close()

    (data_dir / "gyms.json").write_text(
        json.dumps(gyms, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    by_day: dict[str, list] = defaultdict(list)
    for r in snaps:
        day = str(r["bucket_ts"])[:10]
        by_day[day].append(r)

    # Remove stale day files not in DB
    existing = {p.name for p in days_dir.glob("*.csv")}
    wanted = {f"{d}.csv" for d in by_day}
    for stale in existing - wanted:
        (days_dir / stale).unlink()

    for day, rows in by_day.items():
        path = days_dir / f"{day}.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["gym_id", "bucket_ts", "capacity_pct", "is_closed", "scraped_at"])
            for r in rows:
                w.writerow(
                    [
                        r["gym_id"],
                        r["bucket_ts"],
                        "" if r["capacity_pct"] is None else r["capacity_pct"],
                        int(r["is_closed"]),
                        r["scraped_at"],
                    ]
                )

    return {
        "gyms": len(gyms),
        "snapshots": len(snaps),
        "days": len(by_day),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    args = ap.parse_args()
    stats = export(args.db, args.data_dir)
    print(
        f"Exported {stats['snapshots']} snapshots, {stats['gyms']} gyms, "
        f"{stats['days']} day files → {args.data_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

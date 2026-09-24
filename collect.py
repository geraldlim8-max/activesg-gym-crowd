#!/usr/bin/env python3
"""Fetch ActiveSG gym capacities and upsert into SQLite (15-min buckets, SGT)."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SGT = ZoneInfo("Asia/Singapore")
ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "occupancy.db"

API_URL = (
    "https://activesg.gov.sg/api/trpc/pass.getFacilityCapacities"
    "?input=%7B%22json%22%3Anull%2C%22meta%22%3A%7B%22values%22%3A%5B%22undefined%22%5D%7D%7D"
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://activesg.gov.sg/gym-pool-crowd",
}

WINDOW_START = time(7, 0)
WINDOW_END = time(22, 0)  # inclusive of 22:00 bucket only if scraped then; we allow until 22:00


def floor_to_15(dt: datetime) -> datetime:
    """Floor to :00/:15/:30/:45 in the datetime's timezone."""
    minute = (dt.minute // 15) * 15
    return dt.replace(minute=minute, second=0, microsecond=0)


def in_collect_window(dt: datetime) -> bool:
    """True if local time is within 07:00–22:00 inclusive (for 15-min slots)."""
    t = dt.time()
    return WINDOW_START <= t <= WINDOW_END


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS gyms (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gym_id TEXT NOT NULL,
            bucket_ts TEXT NOT NULL,
            capacity_pct INTEGER,
            is_closed INTEGER NOT NULL,
            scraped_at TEXT NOT NULL,
            UNIQUE(gym_id, bucket_ts),
            FOREIGN KEY (gym_id) REFERENCES gyms(id)
        );
        CREATE INDEX IF NOT EXISTS idx_snapshots_bucket ON snapshots(bucket_ts);
        CREATE INDEX IF NOT EXISTS idx_snapshots_gym_bucket ON snapshots(gym_id, bucket_ts);
        """
    )


def fetch_payload() -> dict:
    req = urllib.request.Request(API_URL, headers=HEADERS, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def upsert(conn: sqlite3.Connection, gyms: list[dict], bucket_ts: str, scraped_at: str) -> int:
    saved = 0
    for g in gyms:
        gid = g["id"]
        name = g.get("name") or gid
        pct = g.get("capacityPercentage")
        if pct is not None:
            try:
                pct = int(pct)
            except (TypeError, ValueError):
                pct = None
        is_closed = 1 if g.get("isClosed") else 0

        row = conn.execute("SELECT first_seen FROM gyms WHERE id = ?", (gid,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO gyms (id, name, first_seen, last_seen) VALUES (?, ?, ?, ?)",
                (gid, name, scraped_at, scraped_at),
            )
        else:
            conn.execute(
                "UPDATE gyms SET name = ?, last_seen = ? WHERE id = ?",
                (name, scraped_at, gid),
            )

        conn.execute(
            """
            INSERT INTO snapshots (gym_id, bucket_ts, capacity_pct, is_closed, scraped_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(gym_id, bucket_ts) DO UPDATE SET
                capacity_pct = excluded.capacity_pct,
                is_closed = excluded.is_closed,
                scraped_at = excluded.scraped_at
            """,
            (gid, bucket_ts, pct, is_closed, scraped_at),
        )
        saved += 1
    return saved


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect ActiveSG gym occupancy")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Collect even outside 07:00–22:00 SGT",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
        help="SQLite database path",
    )
    args = parser.parse_args()

    now = datetime.now(SGT)
    bucket = floor_to_15(now)
    bucket_ts = bucket.isoformat(timespec="seconds")
    scraped_at = now.isoformat(timespec="seconds")

    if not args.force and not in_collect_window(now):
        print(
            f"Outside collect window (07:00–22:00 SGT); now={now.strftime('%H:%M %Z')}. "
            "Use --force to override. Skipping."
        )
        return 0

    args.db.parent.mkdir(parents=True, exist_ok=True)

    try:
        payload = fetch_payload()
    except urllib.error.URLError as e:
        print(f"Fetch failed: {e}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {e}", file=sys.stderr)
        return 1

    try:
        gyms = payload["result"]["data"]["json"]["gymFacilities"]
    except (KeyError, TypeError) as e:
        print(f"Unexpected API shape: {e}", file=sys.stderr)
        return 1

    if not isinstance(gyms, list):
        print("gymFacilities is not a list", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    try:
        init_db(conn)
        saved = upsert(conn, gyms, bucket_ts, scraped_at)
        conn.commit()
    finally:
        conn.close()

    open_n = sum(1 for g in gyms if not g.get("isClosed"))
    closed_n = len(gyms) - open_n
    sample = ", ".join(
        f"{g.get('name', '?').replace('ActiveSG Gym @ ', '')}={g.get('capacityPercentage')}%"
        for g in gyms[:3]
    )
    print(
        f"Saved {saved} gyms for bucket {bucket_ts} "
        f"(open={open_n}, closed={closed_n}). Sample: {sample}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

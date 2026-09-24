#!/usr/bin/env python3
"""FastAPI server for ActiveSG gym occupancy dashboard."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

SGT = ZoneInfo("Asia/Singapore")
ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "occupancy.db"
WEB_DIR = ROOT / "web"

app = FastAPI(title="ActiveSG Gym Crowd", docs_url=None, redoc_url=None)


def get_conn() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise HTTPException(status_code=503, detail="Database not ready; run collect.py first")
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


@app.get("/api/gyms")
def api_gyms() -> list[dict[str, Any]]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, name, first_seen, last_seen FROM gyms ORDER BY name"
        ).fetchall()
        return [row_to_dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/latest")
def api_latest() -> dict[str, Any]:
    conn = get_conn()
    try:
        latest_bucket = conn.execute(
            "SELECT MAX(bucket_ts) AS b FROM snapshots"
        ).fetchone()["b"]
        if not latest_bucket:
            return {"bucket_ts": None, "scraped_at": None, "gyms": []}

        rows = conn.execute(
            """
            SELECT s.gym_id AS id, g.name, s.capacity_pct, s.is_closed,
                   s.bucket_ts, s.scraped_at
            FROM snapshots s
            JOIN gyms g ON g.id = s.gym_id
            WHERE s.bucket_ts = ?
            ORDER BY
                CASE WHEN s.is_closed = 1 THEN 1 ELSE 0 END,
                COALESCE(s.capacity_pct, -1) DESC,
                g.name
            """,
            (latest_bucket,),
        ).fetchall()
        scraped = rows[0]["scraped_at"] if rows else None
        gyms = [
            {
                "id": r["id"],
                "name": r["name"],
                "capacity_pct": r["capacity_pct"],
                "is_closed": bool(r["is_closed"]),
            }
            for r in rows
        ]
        return {"bucket_ts": latest_bucket, "scraped_at": scraped, "gyms": gyms}
    finally:
        conn.close()


@app.get("/api/history")
def api_history(
    gym_id: str = Query(...),
    date: Optional[str] = Query(None, description="YYYY-MM-DD in SGT"),
) -> dict[str, Any]:
    if date is None:
        date = datetime.now(SGT).date().isoformat()
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")

    # Full calendar day in SGT; UI charts still focus on 07:00–22:00 slots
    day_start = f"{date}T00:00:00+08:00"
    day_end = f"{date}T23:59:59+08:00"

    conn = get_conn()
    try:
        gym = conn.execute("SELECT id, name FROM gyms WHERE id = ?", (gym_id,)).fetchone()
        if not gym:
            raise HTTPException(status_code=404, detail="gym not found")

        rows = conn.execute(
            """
            SELECT bucket_ts, capacity_pct, is_closed, scraped_at
            FROM snapshots
            WHERE gym_id = ? AND bucket_ts >= ? AND bucket_ts <= ?
            ORDER BY bucket_ts
            """,
            (gym_id, day_start, day_end),
        ).fetchall()

        # Also accept buckets stored without offset if any legacy
        if not rows:
            rows = conn.execute(
                """
                SELECT bucket_ts, capacity_pct, is_closed, scraped_at
                FROM snapshots
                WHERE gym_id = ?
                  AND substr(bucket_ts, 1, 10) = ?
                  AND substr(bucket_ts, 12, 5) >= '07:00'
                  AND substr(bucket_ts, 12, 5) <= '22:00'
                ORDER BY bucket_ts
                """,
                (gym_id, date),
            ).fetchall()

        points = [
            {
                "bucket_ts": r["bucket_ts"],
                "capacity_pct": r["capacity_pct"],
                "is_closed": bool(r["is_closed"]),
                "scraped_at": r["scraped_at"],
            }
            for r in rows
        ]
        return {"gym_id": gym["id"], "name": gym["name"], "date": date, "points": points}
    finally:
        conn.close()


@app.get("/api/heatmap")
def api_heatmap(gym_id: str = Query(...)) -> dict[str, Any]:
    """Average capacity by weekday (0=Mon) and 15-min slot (HH:MM), open snapshots only."""
    conn = get_conn()
    try:
        gym = conn.execute("SELECT id, name FROM gyms WHERE id = ?", (gym_id,)).fetchone()
        if not gym:
            raise HTTPException(status_code=404, detail="gym not found")

        rows = conn.execute(
            """
            SELECT bucket_ts, capacity_pct, is_closed
            FROM snapshots
            WHERE gym_id = ? AND capacity_pct IS NOT NULL
            """,
            (gym_id,),
        ).fetchall()

        # weekday -> slot -> [values]
        buckets: dict[int, dict[str, list[int]]] = {i: {} for i in range(7)}
        for r in rows:
            if r["is_closed"]:
                continue
            ts = r["bucket_ts"]
            try:
                # Handle +08:00 or naive
                if ts.endswith("Z"):
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(SGT)
                else:
                    dt = datetime.fromisoformat(ts)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=SGT)
                    else:
                        dt = dt.astimezone(SGT)
            except ValueError:
                continue
            if dt.hour < 7 or (dt.hour > 22) or (dt.hour == 22 and dt.minute > 0):
                continue
            slot = f"{dt.hour:02d}:{dt.minute:02d}"
            wd = dt.weekday()  # Mon=0
            buckets[wd].setdefault(slot, []).append(int(r["capacity_pct"]))

        cells = []
        for wd in range(7):
            for slot, vals in sorted(buckets[wd].items()):
                cells.append(
                    {
                        "weekday": wd,
                        "slot": slot,
                        "avg_pct": round(sum(vals) / len(vals), 1),
                        "samples": len(vals),
                    }
                )

        return {
            "gym_id": gym["id"],
            "name": gym["name"],
            "cells": cells,
            "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        }
    finally:
        conn.close()


@app.get("/api/dates")
def api_dates(gym_id: Optional[str] = None) -> dict[str, Any]:
    conn = get_conn()
    try:
        if gym_id:
            rows = conn.execute(
                """
                SELECT DISTINCT substr(bucket_ts, 1, 10) AS d
                FROM snapshots WHERE gym_id = ?
                ORDER BY d DESC
                """,
                (gym_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT DISTINCT substr(bucket_ts, 1, 10) AS d
                FROM snapshots ORDER BY d DESC
                """
            ).fetchall()
        return {"dates": [r["d"] for r in rows]}
    finally:
        conn.close()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")


def main() -> None:
    import uvicorn

    uvicorn.run("serve:app", host="0.0.0.0", port=8787, reload=False)


if __name__ == "__main__":
    main()

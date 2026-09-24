#!/usr/bin/env python3
"""Build static GitHub Pages site from data/ + web/ → dist/."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SGT = ZoneInfo("Asia/Singapore")
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
WEB_DIR = ROOT / "web"
DIST_DIR = ROOT / "dist"
CUSTOM_DOMAIN = "gym.llamatiles.com"


def load_rows(data_dir: Path) -> tuple[list[dict], list[dict]]:
    gyms_path = data_dir / "gyms.json"
    gyms = json.loads(gyms_path.read_text(encoding="utf-8")) if gyms_path.exists() else []
    rows: list[dict] = []
    days_dir = data_dir / "days"
    if days_dir.exists():
        for path in sorted(days_dir.glob("*.csv")):
            with path.open(newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    pct = r.get("capacity_pct", "")
                    rows.append(
                        {
                            "gym_id": r["gym_id"],
                            "bucket_ts": r["bucket_ts"],
                            "capacity_pct": None if pct == "" else int(pct),
                            "is_closed": int(r.get("is_closed") or 0),
                            "scraped_at": r.get("scraped_at") or "",
                        }
                    )
    return gyms, rows


def parse_ts(ts: str) -> datetime | None:
    try:
        if ts.endswith("Z"):
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(SGT)
        else:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=SGT)
            else:
                dt = dt.astimezone(SGT)
        return dt
    except ValueError:
        return None


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def build(data_dir: Path, web_dir: Path, dist_dir: Path, domain: str = CUSTOM_DOMAIN) -> dict:
    gyms, rows = load_rows(data_dir)
    gym_by_id = {g["id"]: g for g in gyms}

    if dist_dir.exists():
        shutil.rmtree(dist_dir)
    dist_dir.mkdir(parents=True)

    # Copy frontend
    (dist_dir / "static").mkdir()
    shutil.copy2(web_dir / "index.html", dist_dir / "index.html")
    for name in ("app.js", "styles.css"):
        shutil.copy2(web_dir / "static" / name, dist_dir / "static" / name)
    (dist_dir / "CNAME").write_text(domain + "\n", encoding="utf-8")
    (dist_dir / ".nojekyll").write_text("", encoding="utf-8")

    api = dist_dir / "api"
    write_json(api / "gyms.json", gyms)

    # latest
    latest_bucket = max((r["bucket_ts"] for r in rows), default=None)
    latest_gyms = []
    scraped = None
    if latest_bucket:
        bucket_rows = [r for r in rows if r["bucket_ts"] == latest_bucket]
        scraped = bucket_rows[0]["scraped_at"] if bucket_rows else None
        for r in bucket_rows:
            g = gym_by_id.get(r["gym_id"], {})
            latest_gyms.append(
                {
                    "id": r["gym_id"],
                    "name": g.get("name", r["gym_id"]),
                    "capacity_pct": r["capacity_pct"],
                    "is_closed": bool(r["is_closed"]),
                }
            )
        latest_gyms.sort(
            key=lambda x: (
                1 if x["is_closed"] else 0,
                -(x["capacity_pct"] if x["capacity_pct"] is not None else -1),
                x["name"],
            )
        )
    write_json(
        api / "latest.json",
        {"bucket_ts": latest_bucket, "scraped_at": scraped, "gyms": latest_gyms},
    )

    # index by gym and day
    by_gym_day: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    all_dates: set[str] = set()
    dates_by_gym: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        day = r["bucket_ts"][:10]
        all_dates.add(day)
        dates_by_gym[r["gym_id"]].add(day)
        by_gym_day[r["gym_id"]][day].append(
            {
                "bucket_ts": r["bucket_ts"],
                "capacity_pct": r["capacity_pct"],
                "is_closed": bool(r["is_closed"]),
                "scraped_at": r["scraped_at"],
            }
        )

    write_json(api / "dates.json", {"dates": sorted(all_dates, reverse=True)})

    for gid, g in gym_by_id.items():
        g_dates = sorted(dates_by_gym.get(gid, []), reverse=True)
        write_json(api / "dates" / f"{gid}.json", {"dates": g_dates})
        for day, points in by_gym_day.get(gid, {}).items():
            points = sorted(points, key=lambda p: p["bucket_ts"])
            write_json(
                api / "history" / gid / f"{day}.json",
                {"gym_id": gid, "name": g["name"], "date": day, "points": points},
            )
        # empty history stub not needed

        # heatmap
        buckets: dict[int, dict[str, list[int]]] = {i: {} for i in range(7)}
        for r in rows:
            if r["gym_id"] != gid or r["is_closed"] or r["capacity_pct"] is None:
                continue
            dt = parse_ts(r["bucket_ts"])
            if not dt:
                continue
            if dt.hour < 7 or dt.hour > 22 or (dt.hour == 22 and dt.minute > 0):
                continue
            slot = f"{dt.hour:02d}:{dt.minute:02d}"
            wd = dt.weekday()
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
        write_json(
            api / "heatmap" / f"{gid}.json",
            {
                "gym_id": gid,
                "name": g["name"],
                "cells": cells,
                "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            },
        )

    # meta
    write_json(
        api / "meta.json",
        {
            "generated_at": datetime.now(SGT).isoformat(timespec="seconds"),
            "snapshot_count": len(rows),
            "gym_count": len(gyms),
            "latest_bucket": latest_bucket,
            "timezone": "Asia/Singapore",
        },
    )

    return {
        "snapshots": len(rows),
        "gyms": len(gyms),
        "dates": len(all_dates),
        "latest_bucket": latest_bucket,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    ap.add_argument("--web-dir", type=Path, default=WEB_DIR)
    ap.add_argument("--dist-dir", type=Path, default=DIST_DIR)
    ap.add_argument("--domain", default=CUSTOM_DOMAIN)
    args = ap.parse_args()
    stats = build(args.data_dir, args.web_dir, args.dist_dir, args.domain)
    print(
        f"Built site → {args.dist_dir} "
        f"(snapshots={stats['snapshots']}, gyms={stats['gyms']}, "
        f"dates={stats['dates']}, latest={stats['latest_bucket']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

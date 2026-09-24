#!/usr/bin/env python3
"""One sync cycle: optionally collect, export text data, commit+push to GitHub."""

from __future__ import annotations

import argparse
import fcntl
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SGT = ZoneInfo("Asia/Singapore")
ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "occupancy.db"
LOCK_PATH = ROOT / ".run" / "sync.lock"
LOG_PATH = ROOT / "logs" / "sync.log"
MAX_LOG_BYTES = 1_500_000

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


def log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if LOG_PATH.exists() and LOG_PATH.stat().st_size > MAX_LOG_BYTES:
        rotated = LOG_PATH.with_suffix(".log.1")
        rotated.unlink(missing_ok=True)
        LOG_PATH.rename(rotated)
    line = f"{datetime.now(SGT).isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def floor_to_15(dt: datetime) -> datetime:
    minute = (dt.minute // 15) * 15
    return dt.replace(minute=minute, second=0, microsecond=0)


def slot_exists(conn: sqlite3.Connection, bucket_ts: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM snapshots WHERE bucket_ts = ? LIMIT 1", (bucket_ts,)
    ).fetchone()
    return row is not None


def run(cmd: list[str], retries: int = 1, quiet: bool = False) -> subprocess.CompletedProcess:
    last = None
    for attempt in range(1, retries + 1):
        last = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if last.returncode == 0:
            return last
        err = (last.stderr or last.stdout or "").strip().replace("\n", " | ")
        # Never echo secrets; redact common token patterns lightly
        if "ghp_" in err or "gho_" in err or "Bearer " in err:
            err = "[redacted auth error]"
        log(f"cmd failed (attempt {attempt}/{retries}): {' '.join(cmd[:3])}… rc={last.returncode} {err[:400]}")
        if attempt < retries:
            time.sleep(min(30, 5 * attempt))
    assert last is not None
    return last


def git_output(args: list[str]) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "git failed").strip()[:400])
    return r.stdout


def ensure_git_repo() -> None:
    if not (ROOT / ".git").exists():
        raise SystemExit(f"No git repo at {ROOT}; init/clone first")
    # credentials via gh
    subprocess.run(["gh", "auth", "setup-git"], cwd=ROOT, capture_output=True)


def push_data(message: str) -> bool:
    """Stage data/ (+ web/README/workflows if changed), commit, pull --rebase, push.
    Returns True if a commit was pushed (or created)."""
    ensure_git_repo()
    # Only track publishing paths
    subprocess.run(
        ["git", "add", "data/gyms.json", "data/days", "web", "scripts",
         ".github", "README.md", ".gitignore", "CNAME"],
        cwd=ROOT,
        capture_output=True,
    )
    # CNAME may live only in dist; optional at repo root too
    status = git_output(["status", "--porcelain"])
    if not status.strip():
        log("Nothing to commit")
        return False

    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "activesg-sync"
    env["GIT_AUTHOR_EMAIL"] = "activesg-sync@users.noreply.github.com"
    env["GIT_COMMITTER_NAME"] = env["GIT_AUTHOR_NAME"]
    env["GIT_COMMITTER_EMAIL"] = env["GIT_AUTHOR_EMAIL"]
    c = subprocess.run(
        ["git", "commit", "-m", message],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    if c.returncode != 0:
        log(f"commit failed: {(c.stderr or c.stdout or '')[:300]}")
        return False

    for attempt in range(1, 4):
        pr = subprocess.run(
            ["git", "pull", "--rebase", "--autostash", "origin", "main"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if pr.returncode != 0:
            log(f"pull --rebase failed (attempt {attempt}): {(pr.stderr or pr.stdout or '')[:300]}")
            subprocess.run(["git", "rebase", "--abort"], cwd=ROOT, capture_output=True)
            time.sleep(3 * attempt)
            continue
        push = subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if push.returncode == 0:
            log("Pushed OK")
            return True
        err = (push.stderr or push.stdout or "")
        if "ghp_" in err or "gho_" in err:
            err = "[redacted]"
        log(f"push failed (attempt {attempt}): {err[:300]}")
        time.sleep(5 * attempt)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force-collect", action="store_true",
                    help="Collect even outside 07:00–22:00 SGT / even if slot exists (upsert)")
    ap.add_argument("--force-export", action="store_true",
                    help="Export+push even if no new slot")
    ap.add_argument("--skip-collect", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args()

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_f = LOCK_PATH.open("w")
    try:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("Another sync is running; exiting")
        return 0

    try:
        now = datetime.now(SGT)
        bucket = floor_to_15(now)
        bucket_ts = bucket.isoformat(timespec="seconds")

        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        try:
            # ensure tables exist via collect's init if needed
            from collect import init_db, in_collect_window

            init_db(conn)
            conn.commit()
            exists = slot_exists(conn, bucket_ts)
        finally:
            conn.close()

        collected_new = False
        if args.skip_collect:
            log(f"skip-collect; bucket={bucket_ts} exists={exists}")
        elif exists and not args.force_collect:
            log(f"Slot already filled: {bucket_ts}")
        elif not args.force_collect and not in_collect_window(now):
            log(f"Outside window; now={now.strftime('%H:%M %Z')}")
        else:
            cmd = [sys.executable, str(ROOT / "collect.py")]
            if args.force_collect:
                cmd.append("--force")
            log(f"Collecting for {bucket_ts}…")
            r = run(cmd, retries=3)
            if r.returncode != 0:
                log(f"collect failed: {(r.stderr or r.stdout or '')[:400]}")
                return 1
            log((r.stdout or "").strip() or "collect ok")
            collected_new = not exists or args.force_collect

        if not collected_new and not args.force_export:
            log("No new slot; skipping export/push")
            return 0

        from export_data import export

        stats = export(DB_PATH, ROOT / "data")
        log(
            f"Exported snapshots={stats['snapshots']} gyms={stats['gyms']} days={stats['days']}"
        )

        if args.no_push:
            log("--no-push set; done")
            return 0

        msg = f"data: slot {bucket_ts} ({stats['snapshots']} rows)"
        if not push_data(msg):
            # still ok if nothing to commit after export identical
            return 0
        return 0
    finally:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
        lock_f.close()


if __name__ == "__main__":
    raise SystemExit(main())

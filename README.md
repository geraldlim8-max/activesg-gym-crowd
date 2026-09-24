# ActiveSG Gym Crowd

Unofficial personal tracker for [ActiveSG gym occupancy](https://activesg.gov.sg/gym-pool-crowd) (Singapore).

**Live site:** https://gym.llamatiles.com

## Architecture

ActiveSG sits behind Cloudflare bot protection. Requests from GitHub Actions (Azure) and Cloudflare Workers get **403 “Just a moment…”** challenge pages. Requests from the collection machine succeed.

So this is a **hybrid** setup:

1. **Collector (separate machine)** – `collect.py` writes 15-minute SGT buckets into local SQLite `data/occupancy.db` (07:00–22:00 SGT). A sync daemon exports text data and pushes to GitHub when a new slot is filled.
2. **This repo** – stores `data/gyms.json` + `data/days/YYYY-MM-DD.csv` (not a binary SQLite commit every run).
3. **GitHub Pages** – on each push to `main`, Actions builds static JSON for the dashboard and deploys it.

Occupancy numbers come only from the API; nothing is invented.

## Data model

- Buckets floored to `:00 / :15 / :30 / :45` Asia/Singapore; unique on `(gym_id, bucket_ts)`.
- CSV columns: `gym_id,bucket_ts,capacity_pct,is_closed,scraped_at`

## Local collector / sync

```bash
python3 collect.py                 # skip outside 07:00–22:00 SGT
python3 collect.py --force
python3 scripts/export_data.py     # SQLite → data/
python3 scripts/build_site.py      # data/ + web/ → dist/
python3 scripts/sync_once.py       # collect if needed → export → push
python3 scripts/sync_once.py --force-collect --force-export

# Idempotent ensure / watchdog:
./scripts/ensure_runner.sh
```

- Loop: `scripts/daemon_loop.sh` (~every 4 minutes)
- Logs: `logs/sync.log`, `logs/daemon.log`
- Lock: `.run/sync.lock` · PID: `.run/daemon.pid`

## Local dashboard (optional)

```bash
python3 serve.py   # http://127.0.0.1:8787
```

Frontend uses FastAPI on port 8787 and static `api/*.json` on Pages.

## Attribution

Data: ActiveSG Gym & Pool Crowd — https://activesg.gov.sg/gym-pool-crowd  
Unofficial, personal use.

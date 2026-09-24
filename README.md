# ActiveSG Gym Crowd

Unofficial personal tracker for [ActiveSG gym occupancy](https://activesg.gov.sg/gym-pool-crowd) (Singapore).

## Status

**GitHub Actions runners cannot fetch the ActiveSG API.** Cloudflare returns HTTP 403 with a "Just a moment..." challenge page from Azure-hosted `ubuntu-latest` runners (curl and Python urllib), same as Cloudflare Workers. Header variations (Chrome 120, Chrome 131 + sec-ch-ua, Origin, minimal UA) did not help.

Local collection still works from residential/other IPs. This repo is a placeholder until a reachable fetch path exists (e.g. self-hosted runner, or an IP Cloudflare allows).

## Planned

- Collect every 15 min during 07:00–22:00 SGT
- Static site on GitHub Pages at https://gym.llamatiles.com

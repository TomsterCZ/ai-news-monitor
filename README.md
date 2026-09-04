# AI News Monitor

A public web page with the top 10 AI stories of the last 48 hours, refreshed
daily by a scheduled Claude routine.

Site: https://tomstercz.github.io/ai-news-monitor/

## How it works

1. `pipeline/fetch.py` pulls every enabled feed in `sources.json`, keeps items
   from the last `lookback_hours`, dedupes them, merges with any candidates
   already committed for the day and writes `data/candidates/<date>.json`.
   A GitHub Actions workflow (`.github/workflows/fetch.yml`) runs it every three
   hours because the Claude cloud sandbox cannot reach the feeds directly.
2. A Claude routine (cloud agent) follows `ROUTINE.md`: it reads the
   candidates, discards noise, merges duplicate coverage, scores each story
   for relevance, significance and novelty, and writes `data/daily/<date>.json`.
3. `pipeline/build.py` computes final scores, ranks, and renders the site into
   `docs/` (served by GitHub Pages): `index.html` for the latest edition,
   `archive/<date>.html` for past days and `data/*.json` for machine access.
4. The routine commits and pushes; GitHub Pages redeploys within a minute or two.

Final score = weighted base (0-10)
            + (source_weight - 1) x source_scale
            + corroboration_bonus x number of extra sources covering the story (capped),
clamped to 0-10. All knobs live in `config.json`.

## Tuning

- Add, remove, re-weight or disable sources in `sources.json`. Weights around
  1.0; a lab announcing its own work deserves more than an aggregator.
- Change weights, lookback window, top N or bonuses in `config.json`.
- Edit the selection rules and rubric in `ROUTINE.md`.

## Running locally

```bash
python3 pipeline/fetch.py           # writes data/candidates/<today>.json
# write data/daily/<today>.json by hand or with an LLM following ROUTINE.md
python3 pipeline/build.py           # renders docs/
open docs/index.html
```

Python 3.9+ and no third-party packages required.

## Schedule

The routine runs daily at 04:00 UTC (06:00 Prague in summer time, 05:00 in
winter). Cron for routines is in UTC, so adjust it after the clock change if
the exact local hour matters. The "Refresh now" button on the page opens the
routine on claude.ai where the owner can start an extra run at any time.

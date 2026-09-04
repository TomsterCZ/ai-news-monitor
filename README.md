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

## X posts (right column)

X closed its free API, and every unofficial timeline mirror (Nitter, syndication
widgets, third-party viewers) is dead, stale or blocked as of September 2026.
The free path that works is a two-step one, implemented in `pipeline/fetch_x.py`:

1. Google's Programmable Search JSON API finds each account's post URLs from
   the last two days (one query per account, 100 free queries per day).
2. X's public embed endpoint returns each post's text, date, likes and reply
   count. Reposts and views are not available this way.

Setup (free, about ten minutes):

1. https://programmablesearchengine.google.com > Add. Name it anything, in
   "Sites to search" enter `x.com/*`, create it, and copy the **Search engine ID**.
2. https://developers.google.com/custom-search/v1/overview > "Get a Key".
   Create or pick a Google Cloud project and copy the **API key**.
3. GitHub repository > Settings > Secrets and variables > Actions > New
   repository secret, twice: `GOOGLE_CSE_ID` and `GOOGLE_CSE_KEY`.
4. Actions tab > "Fetch candidates" > Run workflow. Check that
   `data/x_candidates/<today>.json` appears with posts.

The fetch runs at most every five hours (12 queries each) to stay inside the
quota; a manual workflow run always fetches. If an `X_BEARER_TOKEN` secret is
present the official API is used instead, with view counts.

Post score = 0.6 x relevance (judged by the routine) + 0.4 x engagement
(log scale of likes + 3 x reposts + views / 200) + account weight bonus.

## Custom domain

GitHub Pages serves a custom domain for free; only the domain itself costs
money (a `.cz` is about CZK 200-300 per year, a `.com` about $10-15).

1. Buy the domain at any registrar (Cloudflare, Wedos, Forpsi, Namecheap).
2. In DNS add a `CNAME` record for `www` (or a subdomain such as `ai`)
   pointing to `tomstercz.github.io`. For the bare domain add `A` records to
   185.199.108.153, 185.199.109.153, 185.199.110.153, 185.199.111.153.
3. In the repository: Settings > Pages > Custom domain, enter the domain and
   tick "Enforce HTTPS". GitHub writes `docs/CNAME`; commit it.
4. Update `site_url` in `config.json`.

## Tuning

- Add, remove, re-weight or disable sources in `sources.json` and X accounts
  in `x_sources.json`. Weights around 1.0; a lab announcing its own work
  deserves more than an aggregator.
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

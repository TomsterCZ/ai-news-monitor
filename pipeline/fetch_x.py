#!/usr/bin/env python3
"""Fetch recent posts from the X accounts in x_sources.json via the official X API v2.

Standard library only. Needs the environment variable X_BEARER_TOKEN; without it
the script prints a notice and exits 0 so the rest of the pipeline keeps working.

Pay-per-use pricing (2026): about $0.005 per post read, $0.01 per user lookup.
User ids are cached in data/x_users.json so each handle is looked up once.

Usage:  python3 pipeline/fetch_x.py [--date YYYY-MM-DD] [--lookback HOURS] [--per-account N]
Output: data/x_candidates/<date>.json
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.x.com/2"


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def api_get(path, params, token):
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": "AI-News-Monitor/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def expand_text(tweet):
    text = tweet.get("text", "")
    for u in (tweet.get("entities") or {}).get("urls", []):
        if u.get("url") and u.get("expanded_url"):
            text = text.replace(u["url"], u["expanded_url"])
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    ap.add_argument("--lookback", type=int, default=None)
    ap.add_argument("--per-account", type=int, default=None)
    args = ap.parse_args()

    token = os.environ.get("X_BEARER_TOKEN", "").strip()
    if not token:
        print("X_BEARER_TOKEN not set; skipping X fetch (the site will show no posts until it is configured)")
        return 0

    config = load_json(ROOT / "config.json")
    accounts = [a for a in load_json(ROOT / "x_sources.json")["accounts"] if a.get("enabled", True)]
    lookback = args.lookback or config["lookback_hours"]
    per_account = args.per_account or config.get("x_posts_per_account", 10)
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=lookback)

    # Resolve user ids (cached; one paid lookup per new handle).
    cache_path = ROOT / "data" / "x_users.json"
    users = load_json(cache_path, {})
    missing = [a["handle"] for a in accounts if a["handle"].lower() not in users]
    if missing:
        try:
            data = api_get("/users/by", {"usernames": ",".join(missing), "user.fields": "public_metrics,name"}, token)
            for u in data.get("data", []):
                users[u["username"].lower()] = {"id": u["id"], "name": u.get("name"), "followers": (u.get("public_metrics") or {}).get("followers_count")}
            for e in data.get("errors", []):
                print(f"  user lookup failed: {e.get('value')} {e.get('title')}")
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(users, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        except urllib.error.HTTPError as exc:
            print(f"ERROR resolving users: HTTP {exc.code} {exc.read()[:200]!r}", file=sys.stderr)
            return 1

    report, posts = [], []
    for a in accounts:
        u = users.get(a["handle"].lower())
        if not u:
            report.append({"handle": a["handle"], "ok": False, "fetched": 0, "error": "unknown handle"})
            continue
        try:
            data = api_get(f"/users/{u['id']}/tweets", {
                "max_results": max(5, min(100, per_account)),
                "exclude": "retweets,replies",
                "start_time": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "tweet.fields": "public_metrics,created_at,entities,lang",
            }, token)
        except urllib.error.HTTPError as exc:
            report.append({"handle": a["handle"], "ok": False, "fetched": 0, "error": f"HTTP {exc.code}"})
            continue
        except Exception as exc:  # noqa: BLE001
            report.append({"handle": a["handle"], "ok": False, "fetched": 0, "error": f"{type(exc).__name__}: {str(exc)[:120]}"})
            continue
        items = data.get("data", [])
        for t in items:
            m = t.get("public_metrics") or {}
            posts.append({
                "id": t["id"],
                "url": f"https://x.com/{a['handle']}/status/{t['id']}",
                "author_handle": a["handle"],
                "author_name": u.get("name") or a.get("name"),
                "author_followers": u.get("followers"),
                "author_weight": a.get("weight", 1.0),
                "group": a.get("group", ""),
                "created_at": t.get("created_at"),
                "lang": t.get("lang"),
                "text": expand_text(t),
                "metrics": {
                    "likes": m.get("like_count", 0),
                    "reposts": m.get("retweet_count", 0) + m.get("quote_count", 0),
                    "replies": m.get("reply_count", 0),
                    "views": m.get("impression_count", 0),
                    "bookmarks": m.get("bookmark_count", 0),
                },
            })
        report.append({"handle": a["handle"], "ok": True, "fetched": len(items), "error": ""})

    out_path = ROOT / "data" / "x_candidates" / f"{args.date}.json"
    previous = (load_json(out_path, {}) or {}).get("posts", [])
    merged = {}
    for p in previous + posts:  # newer fetch wins (fresher metrics)
        created = p.get("created_at")
        if created:
            try:
                if datetime.fromisoformat(created.replace("Z", "+00:00")) < since:
                    continue
            except ValueError:
                pass
        merged[p["id"]] = p
    final = sorted(merged.values(), key=lambda p: p.get("created_at") or "", reverse=True)

    out = {
        "date": args.date,
        "fetched_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "lookback_hours": lookback,
        "accounts_total": len(accounts),
        "accounts_ok": sum(1 for r in report if r["ok"]),
        "account_report": report,
        "posts": final,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Wrote {out_path.relative_to(ROOT)}: {len(final)} posts; this fetch reached {out['accounts_ok']}/{len(accounts)} accounts")
    for r in report:
        print(f"  @{r['handle']:<16} " + (f"{r['fetched']:3d} posts" if r["ok"] else f"FAILED {r['error']}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Fetch recent posts from the X accounts in x_sources.json.

Modes, standard library only, chosen from environment variables:

1. Google discovery (free): needs GOOGLE_CSE_KEY and GOOGLE_CSE_ID (a
   Programmable Search Engine restricted to x.com). One query per account
   finds post URLs from the last two days; each post's text, date and like
   count then come from X's public embed endpoint (cdn.syndication.twimg.com).
   Reposts and views are not available in this mode. Google's free quota is
   100 queries per day, so the fetch is skipped when the last one is recent
   (see --min-interval-hours).

2. Official X API v2 (X_BEARER_TOKEN set): pay-per-use, includes views.

Usage:  python3 pipeline/fetch_x.py [--date YYYY-MM-DD] [--lookback HOURS] [--force]
Output: data/x_candidates/<date>.json
"""
import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.x.com/2"
CSE = "https://www.googleapis.com/customsearch/v1"
EMBED = "https://cdn.syndication.twimg.com/tweet-result?id={id}&token=a"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
STATUS_RE = re.compile(r"(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/status/(\d{15,25})")


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def http_get(url, headers, timeout=30):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def expand_text(text, entities):
    for u in (entities or {}).get("urls", []):
        if u.get("url") and u.get("expanded_url"):
            text = text.replace(u["url"], u["expanded_url"])
    return html.unescape(text)


# ---------- Google discovery + embed metrics ----------

def google_post_ids(handle, key, cx, days):
    """Return post ids for @handle found by Google in the last `days` days."""
    params = {"key": key, "cx": cx, "q": f"site:x.com/{handle}/status", "dateRestrict": f"d{days}", "sort": "date", "num": 10}
    data = json.loads(http_get(f"{CSE}?{urllib.parse.urlencode(params)}", {"User-Agent": "AI-News-Monitor/1.0"}))
    ids = []
    for item in data.get("items", []):
        m = STATUS_RE.search(item.get("link", ""))
        if m and m.group(1).lower() == handle.lower() and m.group(2) not in ids:
            ids.append(m.group(2))
    return ids


def embed_post(post_id):
    """Text, date and like count of one post from X's public embed endpoint."""
    raw = http_get(EMBED.format(id=post_id), {"User-Agent": UA}, timeout=20)
    if not raw.strip():
        return None
    d = json.loads(raw)
    if d.get("__typename") == "TweetTombstone" or "id_str" not in d:
        return None
    text = (d.get("note_tweet") or {}).get("text") or d.get("text") or ""
    return {
        "id": d["id_str"],
        "created_at": d.get("created_at"),
        "lang": d.get("lang"),
        "text": expand_text(text, d.get("entities")),
        "author_handle_actual": (d.get("user") or {}).get("screen_name"),
        "author_name": (d.get("user") or {}).get("name"),
        "likes": d.get("favorite_count", 0) or 0,
        "replies": d.get("conversation_count", 0) or 0,
    }


def fetch_google(accounts, since, key, cx, days, pause):
    results = []
    for a in accounts:
        handle = a["handle"]
        try:
            ids = google_post_ids(handle, key, cx, days)
        except urllib.error.HTTPError as exc:
            body = exc.read()[:200].decode("utf-8", "replace")
            results.append((a, None, f"Google HTTP {exc.code}: {body}"))
            if exc.code in (403, 429):
                break  # quota exhausted; stop burning requests
            continue
        except Exception as exc:  # noqa: BLE001
            results.append((a, None, f"Google {type(exc).__name__}: {str(exc)[:120]}"))
            continue
        posts = []
        for pid in ids:
            try:
                e = embed_post(pid)
            except Exception as exc:  # noqa: BLE001
                print(f"  embed {pid} failed: {type(exc).__name__}: {str(exc)[:80]}")
                e = None
            time.sleep(pause)
            if not e or not e.get("created_at"):
                continue
            try:
                created = datetime.fromisoformat(e["created_at"].replace("Z", "+00:00")).astimezone(timezone.utc)
            except ValueError:
                continue
            if created < since:
                continue
            posts.append({
                "id": e["id"],
                "url": f"https://x.com/{handle}/status/{e['id']}",
                "author_handle": handle,
                "author_name": e.get("author_name") or a.get("name"),
                "author_followers": None,
                "author_weight": a.get("weight", 1.0),
                "group": a.get("group", ""),
                "created_at": created.isoformat().replace("+00:00", "Z"),
                "lang": e.get("lang"),
                "text": e["text"],
                "metrics": {"likes": e["likes"], "reposts": None, "replies": e["replies"], "views": None, "bookmarks": None},
            })
        results.append((a, posts, "" if ids else "Google found no recent posts"))
    return results


# ---------- official API mode ----------

def api_get(path, params, token):
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    return json.loads(http_get(url, {"Authorization": f"Bearer {token}", "User-Agent": "AI-News-Monitor/1.0"}))


def fetch_api(accounts, since, per_account, token):
    cache_path = ROOT / "data" / "x_users.json"
    users = load_json(cache_path, {})
    missing = [a["handle"] for a in accounts if a["handle"].lower() not in users]
    if missing:
        data = api_get("/users/by", {"usernames": ",".join(missing), "user.fields": "public_metrics,name"}, token)
        for u in data.get("data", []):
            users[u["username"].lower()] = {"id": u["id"], "name": u.get("name"), "followers": (u.get("public_metrics") or {}).get("followers_count")}
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(users, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    results = []
    for a in accounts:
        u = users.get(a["handle"].lower())
        if not u:
            results.append((a, None, "unknown handle"))
            continue
        try:
            data = api_get(f"/users/{u['id']}/tweets", {
                "max_results": max(5, min(100, per_account)), "exclude": "retweets,replies",
                "start_time": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "tweet.fields": "public_metrics,created_at,entities,lang",
            }, token)
        except Exception as exc:  # noqa: BLE001
            results.append((a, None, f"{type(exc).__name__}: {str(exc)[:120]}"))
            continue
        posts = []
        for t in data.get("data", []):
            m = t.get("public_metrics") or {}
            posts.append({
                "id": t["id"], "url": f"https://x.com/{a['handle']}/status/{t['id']}",
                "author_handle": a["handle"], "author_name": u.get("name") or a.get("name"),
                "author_followers": u.get("followers"), "author_weight": a.get("weight", 1.0), "group": a.get("group", ""),
                "created_at": t.get("created_at"), "lang": t.get("lang"),
                "text": expand_text(t.get("text", ""), t.get("entities")), "is_quote": False,
                "metrics": {"likes": m.get("like_count", 0), "reposts": m.get("retweet_count", 0) + m.get("quote_count", 0),
                            "replies": m.get("reply_count", 0), "views": m.get("impression_count", 0), "bookmarks": m.get("bookmark_count", 0)},
            })
        results.append((a, posts, ""))
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    ap.add_argument("--lookback", type=int, default=None)
    ap.add_argument("--pause", type=float, default=1.0, help="seconds between embed requests")
    ap.add_argument("--min-interval-hours", type=float, default=5.0, help="skip if the last Google fetch is younger than this")
    ap.add_argument("--force", action="store_true", help="fetch even if the last fetch is recent")
    args = ap.parse_args()

    config = load_json(ROOT / "config.json")
    accounts = [a for a in load_json(ROOT / "x_sources.json")["accounts"] if a.get("enabled", True)]
    lookback = args.lookback or config["lookback_hours"]
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=lookback)
    token = os.environ.get("X_BEARER_TOKEN", "").strip()
    gkey = os.environ.get("GOOGLE_CSE_KEY", "").strip()
    gcx = os.environ.get("GOOGLE_CSE_ID", "").strip()
    out_path = ROOT / "data" / "x_candidates" / f"{args.date}.json"
    previous_file = load_json(out_path, {}) or {}

    if token:
        mode = "api"
        results = fetch_api(accounts, since, config.get("x_posts_per_account", 10), token)
    elif gkey and gcx:
        mode = "google"
        last = previous_file.get("fetched_at")
        if last and not args.force:
            age_h = (now - datetime.fromisoformat(last.replace("Z", "+00:00"))).total_seconds() / 3600
            if age_h < args.min_interval_hours:
                print(f"Last X fetch was {age_h:.1f}h ago (< {args.min_interval_hours}h); skipping to save Google quota. Use --force to override.")
                return 0
        days = max(1, -(-lookback // 24))
        results = fetch_google(accounts, since, gkey, gcx, days, args.pause)
    else:
        print("Neither X_BEARER_TOKEN nor GOOGLE_CSE_KEY/GOOGLE_CSE_ID set; skipping X fetch (the site shows no posts until one is configured)")
        return 0

    report, posts = [], []
    for a, got, err in results:
        if got is None:
            report.append({"handle": a["handle"], "ok": False, "fetched": 0, "error": err})
        else:
            report.append({"handle": a["handle"], "ok": True, "fetched": len(got), "error": ""})
            posts.extend(got)

    previous = previous_file.get("posts", [])
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
        "mode": mode,
        "lookback_hours": lookback,
        "accounts_total": len(accounts),
        "accounts_ok": len({r["handle"] for r in report if r["ok"]} | {p["author_handle"] for p in final}),
        "accounts_reached_now": sum(1 for r in report if r["ok"]),
        "account_report": report,
        "posts": final,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Wrote {out_path.relative_to(ROOT)}: {len(final)} posts ({mode}); this fetch reached {out['accounts_reached_now']}/{len(accounts)} accounts")
    for r in report:
        print(f"  @{r['handle']:<16} " + (f"{r['fetched']:3d} posts in window" if r["ok"] else f"FAILED {r['error']}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

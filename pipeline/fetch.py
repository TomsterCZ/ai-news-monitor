#!/usr/bin/env python3
"""Fetch every enabled source in sources.json and write a candidate list.

Standard library only, so it runs anywhere without installing packages.

Usage:  python3 pipeline/fetch.py [--date YYYY-MM-DD] [--lookback HOURS]
Output: data/candidates/<date>.json
"""
import argparse
import concurrent.futures as cf
import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = "AI-News-Monitor/1.0 (+https://github.com/TomsterCZ/ai-news-monitor)"
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def clean_text(text, limit=600):
    if not text:
        return ""
    text = html.unescape(TAG_RE.sub(" ", text))
    text = WS_RE.sub(" ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return text


def parse_date(value):
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def strip_ns(tag):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def child_text(node, *names):
    for child in node:
        if strip_ns(child.tag) in names and child.text:
            return child.text
    return ""


def atom_link(node):
    fallback = ""
    for child in node:
        if strip_ns(child.tag) != "link":
            continue
        href = child.get("href")
        if not href:
            continue
        if child.get("rel", "alternate") == "alternate":
            return href
        fallback = fallback or href
    return fallback


def parse_feed(raw):
    root = ET.fromstring(raw)
    items = []
    for node in root.iter():
        kind = strip_ns(node.tag)
        if kind == "item":  # RSS 2.0
            items.append({
                "title": clean_text(child_text(node, "title"), 300),
                "link": (child_text(node, "link") or "").strip(),
                "published": child_text(node, "pubDate", "date", "published"),
                "summary": clean_text(child_text(node, "description", "encoded", "summary")),
            })
        elif kind == "entry":  # Atom
            items.append({
                "title": clean_text(child_text(node, "title"), 300),
                "link": atom_link(node),
                "published": child_text(node, "published", "updated"),
                "summary": clean_text(child_text(node, "summary", "content")),
            })
    return items


def normalize_url(url):
    url = url.strip()
    url = re.sub(r"[?&](utm_[^&]+|ref|source)=[^&]*", "", url)
    url = url.rstrip("?&#/")
    return url.lower()


def fetch_source(source, since, cap):
    result = {"name": source["name"], "ok": False, "fetched": 0, "kept": 0, "error": "", "items": []}
    req = urllib.request.Request(source["url"], headers={"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read()
        items = parse_feed(raw)
    except Exception as exc:  # noqa: BLE001 - report every failure, never crash the run
        result["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        return result
    result["ok"] = True
    result["fetched"] = len(items)
    kept = []
    for it in items:
        dt = parse_date(it["published"])
        if not it["link"] or not it["title"]:
            continue
        if dt is not None and dt < since:
            continue
        kept.append({
            "title": it["title"],
            "url": it["link"].strip(),
            "source": source["name"],
            "source_type": source["type"],
            "source_weight": source["weight"],
            "published": dt.isoformat().replace("+00:00", "Z") if dt else None,
            "summary": it["summary"],
        })
    kept.sort(key=lambda x: x["published"] or "", reverse=True)
    result["items"] = kept[:cap]
    result["kept"] = len(result["items"])
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    ap.add_argument("--lookback", type=int, default=None, help="hours; overrides config.json")
    args = ap.parse_args()

    config = load_json(ROOT / "config.json")
    sources = [s for s in load_json(ROOT / "sources.json")["sources"] if s.get("enabled", True)]
    lookback = args.lookback or config["lookback_hours"]
    cap = config.get("max_items_per_source", 40)
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=lookback)

    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda s: fetch_source(s, since, cap), sources))

    # Merge with an existing candidates file for the same date (written earlier by
    # GitHub Actions or a previous run) so a blocked or flaky network never
    # replaces good data with an empty list.
    out_path = ROOT / "data" / "candidates" / f"{args.date}.json"
    previous = []
    if out_path.exists():
        try:
            prev = load_json(out_path)
            previous = prev.get("candidates", [])
            print(f"Merging with existing {out_path.name} ({len(previous)} candidates fetched at {prev.get('fetched_at')})")
        except (ValueError, KeyError):
            previous = []
    merged = [{"items": r["items"]} for r in results] + [{"items": previous}]

    seen_urls, seen_titles, candidates = set(), set(), []
    for r in merged:
        for it in r["items"]:
            dt = parse_date(it.get("published")) if it.get("published") else None
            if dt is not None and dt < since:
                continue
            key_u = normalize_url(it["url"])
            key_t = re.sub(r"[^a-z0-9]+", " ", it["title"].lower()).strip()
            if key_u in seen_urls or key_t in seen_titles:
                continue
            seen_urls.add(key_u)
            seen_titles.add(key_t)
            candidates.append(it)
    candidates.sort(key=lambda x: x["published"] or "", reverse=True)
    for i, c in enumerate(candidates, 1):
        c["id"] = i

    report = [{k: r[k] for k in ("name", "ok", "fetched", "kept", "error")} for r in results]
    out = {
        "date": args.date,
        "fetched_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "lookback_hours": lookback,
        "sources_total": len(sources),
        "sources_ok": len({r["name"] for r in results if r["ok"]} | {c["source"] for c in candidates}),
        "sources_reached_now": sum(1 for r in results if r["ok"]),
        "source_report": report,
        "candidates": candidates,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print(f"Wrote {out_path.relative_to(ROOT)}: {len(candidates)} candidates; this fetch reached {out['sources_ok']}/{len(sources)} sources (lookback {lookback}h)")
    if out["sources_ok"] == 0 and not candidates:
        print("ERROR: no source reachable and no previous candidates for this date", file=sys.stderr)
        return 1
    for r in report:
        status = f"{r['kept']:3d} kept / {r['fetched']:3d} fetched" if r["ok"] else f"FAILED {r['error']}"
        print(f"  {r['name']:<22} {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

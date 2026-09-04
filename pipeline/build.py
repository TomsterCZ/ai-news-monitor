#!/usr/bin/env python3
"""Render the website from scored daily files.

Reads  data/daily/*.json  (one file per day, written by the scoring step)
Writes docs/index.html, docs/archive/<date>.html, docs/data/latest.json, docs/data/<date>.json

Usage: python3 pipeline/build.py
Exits non-zero if the newest daily file fails validation.
"""
import html
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
REQUIRED = ("title", "url", "source", "type", "summary", "scores")
POST_REQUIRED = ("id", "url", "author_handle", "text", "metrics", "scores")
URL_RE = re.compile(r"https?://[^\s<>\"]+")


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def esc(s):
    return html.escape(str(s or ""), quote=True)


def score_ok(v):
    return isinstance(v, (int, float)) and 0 <= v <= 10


def validate(day, config):
    problems = []
    if not isinstance(day.get("items"), list) or not day["items"]:
        return ["items list is missing or empty"]
    cand_path = ROOT / "data" / "candidates" / f"{day.get('date')}.json"
    known_urls = {c["url"] for c in load_json(cand_path)["candidates"]} if cand_path.exists() else None
    for i, it in enumerate(day["items"], 1):
        for key in REQUIRED:
            if key not in it or it[key] in ("", None):
                problems.append(f"item {i}: missing '{key}'")
        if it.get("type") not in config["types"]:
            problems.append(f"item {i}: type '{it.get('type')}' not in config.types")
        sc = it.get("scores") or {}
        for key in ("relevance", "significance", "novelty"):
            if not score_ok(sc.get(key)):
                problems.append(f"item {i}: scores.{key} must be a number 0-10")
        if not str(it.get("url", "")).startswith("http"):
            problems.append(f"item {i}: url must start with http")
        elif known_urls is not None and it["url"] not in known_urls:
            problems.append(f"item {i}: url is not in {cand_path.name}; copy urls verbatim from the candidates file")

    posts = day.get("posts") or []
    if not isinstance(posts, list):
        return problems + ["posts must be a list"]
    x_path = ROOT / "data" / "x_candidates" / f"{day.get('date')}.json"
    known_ids = {p["id"] for p in load_json(x_path)["posts"]} if x_path.exists() else None
    for i, p in enumerate(posts, 1):
        for key in POST_REQUIRED:
            if key not in p or p[key] in ("", None):
                problems.append(f"post {i}: missing '{key}'")
        if not score_ok((p.get("scores") or {}).get("relevance")):
            problems.append(f"post {i}: scores.relevance must be a number 0-10")
        if known_ids is not None and p.get("id") not in known_ids:
            problems.append(f"post {i}: id {p.get('id')} is not in {x_path.name}; copy posts verbatim from the X candidates file")
    return problems


def final_score(item, config, source_weights):
    w = config["weights"]
    sc = item["scores"]
    base = w["relevance"] * sc["relevance"] + w["significance"] * sc["significance"] + w["novelty"] * sc["novelty"]
    weight = item.get("source_weight") or source_weights.get(item["source"], 1.0)
    extra = min(len(item.get("also_covered_by") or []), config["corroboration_max_sources"])
    score = base + (weight - 1.0) * config.get("source_scale", 4.0) + config["corroboration_bonus"] * extra
    return round(max(0.0, min(10.0, score)), 1), round(base, 2), weight, extra


def engagement_score(metrics):
    m = metrics or {}
    raw = 1 + m.get("likes", 0) + 3 * m.get("reposts", 0) + m.get("views", 0) / 200
    return round(min(10.0, 2.2 * math.log10(raw)), 1)


def post_score(post, config, account_weights):
    pw = config.get("post_weights", {"relevance": 0.6, "engagement": 0.4})
    eng = engagement_score(post.get("metrics"))
    weight = post.get("author_weight") or account_weights.get(post["author_handle"].lower(), 1.0)
    score = pw["relevance"] * post["scores"]["relevance"] + pw["engagement"] * eng + (weight - 1.0) * 2.0
    return round(max(0.0, min(10.0, score)), 1), eng, weight


def enrich(day, config, source_weights, account_weights):
    for it in day["items"]:
        it["score"], it["score_base"], it["source_weight"], it["corroborations"] = final_score(it, config, source_weights)
    day["items"].sort(key=lambda x: (-x["score"], -x["scores"]["significance"], -x["scores"]["relevance"], -x["scores"]["novelty"]))
    day["items"] = day["items"][: config["top_n"]]
    for rank, it in enumerate(day["items"], 1):
        it["rank"] = rank
    posts = day.get("posts") or []
    for p in posts:
        p["score"], p["engagement"], p["author_weight"] = post_score(p, config, account_weights)
    posts.sort(key=lambda p: (-p["score"], -(p["metrics"].get("likes", 0))))
    day["posts"] = posts[: config.get("top_posts", 10)]
    for rank, p in enumerate(day["posts"], 1):
        p["rank"] = rank
    return day


def fmt_local(iso, tz):
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(tz).strftime("%-d %b %Y, %H:%M")
    except ValueError:
        return iso


def fmt_num(n):
    n = n or 0
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n/1000:.0f}K"
    if n >= 1000:
        return f"{n/1000:.1f}K"
    return str(n)


def linkify(text):
    out, pos = [], 0
    for m in URL_RE.finditer(text):
        out.append(esc(text[pos:m.start()]))
        url = m.group(0)
        out.append(f'<a href="{esc(url)}" target="_blank" rel="noopener">{esc(url[:60] + ("…" if len(url) > 60 else ""))}</a>')
        pos = m.end()
    out.append(esc(text[pos:]))
    return "".join(out).replace("\n", "<br>")


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--text:#16181d;--muted:#5f6672;--line:#e3e6ea;--accent:#2f6fed;--accent-soft:#e8f0ff;--chip:#eef1f5;--good:#1c8f4e;--x:#111}
@media(prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--text:#e8eaef;--muted:#9aa3b2;--line:#262b35;--accent:#6b9cff;--accent-soft:#1c2740;--chip:#222734;--good:#4cc47f;--x:#e8eaef}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:1380px;margin:0 auto;padding:24px 20px 60px}
header{display:flex;flex-wrap:wrap;gap:12px 20px;align-items:flex-end;justify-content:space-between;margin-bottom:6px}
h1{font-size:28px;margin:0;letter-spacing:-.02em}h1 small{display:block;font-size:14px;font-weight:400;color:var(--muted);margin-top:4px}
.actions{display:flex;gap:8px;flex-wrap:wrap}
.btn{display:inline-block;padding:9px 14px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--text);font-size:14px;font-weight:500}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}.btn:hover{text-decoration:none;filter:brightness(1.05)}
.meta{color:var(--muted);font-size:13px;margin:4px 0 20px}
.layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:28px;align-items:start}
@media(max-width:960px){.layout{grid-template-columns:1fr}}
.col h3{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:0 0 12px;font-weight:600}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:12px;display:grid;grid-template-columns:44px 1fr;gap:12px}
.rank{font-size:20px;font-weight:700;color:var(--muted);text-align:center;padding-top:2px}
.score{font-size:12px;font-weight:600;color:var(--good);text-align:center;margin-top:3px}
.tags{display:flex;flex-wrap:wrap;gap:6px;font-size:12px;margin-bottom:5px}
.chip{background:var(--chip);border-radius:999px;padding:2px 9px;color:var(--muted)}.chip.type{background:var(--accent-soft);color:var(--accent);font-weight:600;text-transform:capitalize}
h2{font-size:17px;margin:0 0 5px;line-height:1.3}h2 a{color:var(--text)}
.summary{margin:0 0 6px}.why{margin:0 0 6px;color:var(--muted);font-size:13.5px}.why b{color:var(--text);font-weight:600}
details{font-size:12.5px;color:var(--muted)}summary{cursor:pointer}details table{border-collapse:collapse;margin-top:6px}details td{padding:1px 12px 1px 0}
.also{font-size:12.5px;color:var(--muted)}
.post{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:12px}
.post .who{display:flex;justify-content:space-between;gap:10px;align-items:baseline;margin-bottom:6px}
.post .who b{font-size:15px}.post .who span{color:var(--muted);font-size:12.5px}
.post .text{white-space:normal;word-wrap:break-word;margin:0 0 8px}
.post .metrics{display:flex;flex-wrap:wrap;gap:12px;font-size:12.5px;color:var(--muted)}
.post .metrics .s{color:var(--good);font-weight:600}
.empty{background:var(--card);border:1px dashed var(--line);border-radius:12px;padding:22px;color:var(--muted);font-size:14px}
footer{margin-top:36px;padding-top:18px;border-top:1px solid var(--line);color:var(--muted);font-size:13px}
footer ul{columns:3;padding-left:18px;margin:6px 0}@media(max-width:700px){footer ul{columns:1}.card{grid-template-columns:36px 1fr;padding:12px}}
.archive{display:flex;flex-wrap:wrap;gap:8px}.archive a{font-size:13px;padding:4px 10px;border:1px solid var(--line);border-radius:6px;background:var(--card)}
.notice{background:var(--accent-soft);border-radius:8px;padding:10px 14px;font-size:14px;margin-bottom:18px}
"""


def render_item(it):
    sc = it["scores"]
    also = it.get("also_covered_by") or []
    also_html = f'<div class="also">Also covered by: {esc(", ".join(also))}</div>' if also else ""
    why = f'<p class="why"><b>Why it matters:</b> {esc(it["why_it_matters"])}</p>' if it.get("why_it_matters") else ""
    pub = f'<span class="chip">{esc(it["published_local"])}</span>' if it.get("published_local") else ""
    bonus = round((it["source_weight"] - 1) * 4.0, 1)
    return f"""
<article class="card">
  <div><div class="rank">{it["rank"]}</div><div class="score">{it["score"]:.1f}</div></div>
  <div>
    <div class="tags"><span class="chip type">{esc(it["type"])}</span><span class="chip">{esc(it["source"])}</span>{pub}</div>
    <h2><a href="{esc(it["url"])}" target="_blank" rel="noopener">{esc(it["title"])}</a></h2>
    <p class="summary">{esc(it["summary"])}</p>
    {why}
    {also_html}
    <details><summary>Score {it["score"]:.1f} / 10</summary>
      <table>
        <tr><td>Relevance</td><td>{sc["relevance"]}</td></tr>
        <tr><td>Significance</td><td>{sc["significance"]}</td></tr>
        <tr><td>Novelty</td><td>{sc["novelty"]}</td></tr>
        <tr><td>Source weight</td><td>{it["source_weight"]} ({"+" if bonus >= 0 else ""}{bonus})</td></tr>
        <tr><td>Corroboration</td><td>+{it["corroborations"]} source(s)</td></tr>
      </table>
    </details>
  </div>
</article>"""


def render_post(p):
    m = p.get("metrics") or {}
    note = f'<p class="why"><b>Why it matters:</b> {esc(p["why_it_matters"])}</p>' if p.get("why_it_matters") else ""
    return f"""
<article class="post">
  <div class="who"><div><b>{esc(p.get("author_name") or p["author_handle"])}</b> <span>@{esc(p["author_handle"])}</span></div><span>{esc(p.get("created_local", ""))}</span></div>
  <p class="text">{linkify(p["text"])}</p>
  {note}
  <div class="metrics">
    <span title="Likes">♥ {fmt_num(m.get("likes"))}</span>
    <span title="Reposts and quotes">↻ {fmt_num(m.get("reposts"))}</span>
    <span title="Views">◉ {fmt_num(m.get("views"))}</span>
    <span class="s" title="Relevance {p["scores"]["relevance"]} · engagement {p["engagement"]}">score {p["score"]:.1f}</span>
    <a href="{esc(p["url"])}" target="_blank" rel="noopener">Open on X</a>
  </div>
</article>"""


def render_page(day, config, all_dates, sources, accounts, is_latest, tz):
    title = config["site_title"]
    date_label = datetime.strptime(day["date"], "%Y-%m-%d").strftime("%A, %-d %B %Y")
    prefix = "" if is_latest else "../"
    refresh = config.get("refresh_url") or ""
    refresh_btn = f'<a class="btn primary" href="{esc(refresh)}" target="_blank" rel="noopener" title="Opens the Claude routine; press Run to refresh now">Refresh now</a>' if refresh else ""
    latest_btn = "" if is_latest else f'<a class="btn" href="{prefix}index.html">Latest</a>'
    archive_links = " ".join(
        f'<a href="{prefix}archive/{d}.html">{d}</a>' if d != day["date"] else f'<a href="#" style="opacity:.5">{d}</a>'
        for d in all_dates
    )
    generated = fmt_local(day.get("generated_at"), tz)
    src_list = "".join(f"<li>{esc(s['name'])} <span style='opacity:.7'>({s['weight']})</span></li>" for s in sources if s.get("enabled", True))
    acc_list = "".join(f"<li>@{esc(a['handle'])} <span style='opacity:.7'>({a['weight']})</span></li>" for a in accounts if a.get("enabled", True))
    notice = "" if is_latest else f'<div class="notice">Archive edition for {esc(date_label)}. <a href="{prefix}index.html">See the latest edition</a>.</div>'
    items_html = "\n".join(render_item(it) for it in day["items"])
    posts = day.get("posts") or []
    posts_html = "\n".join(render_post(p) for p in posts) if posts else '<div class="empty">No X posts in this edition yet. Posts appear here once the X API key is configured for the fetch workflow.</div>'
    considered = day.get("candidates_considered")
    stats = f' · {considered} articles from {day.get("sources_ok", "?")} sources' if considered else ""
    if day.get("posts_considered"):
        stats += f' · {day["posts_considered"]} posts from {day.get("accounts_ok", "?")} X accounts'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · {esc(day["date"])}</title><style>{CSS}</style></head>
<body><div class="wrap">
<header>
  <h1>{esc(title)}<small>Top AI stories and posts for {esc(date_label)}</small></h1>
  <div class="actions">{latest_btn}{refresh_btn}</div>
</header>
<div class="meta">Updated {esc(generated)} ({esc(config["timezone"])}) · last {day.get("lookback_hours", config["lookback_hours"])} hours{stats}</div>
{notice}
<div class="layout">
  <section class="col"><h3>Top {len(day["items"])} articles</h3>{items_html}</section>
  <section class="col"><h3>Top {len(posts) or ""} posts on X</h3>{posts_html}</section>
</div>
<footer>
  <div><b>Archive</b></div><div class="archive" style="margin:8px 0 18px">{archive_links}</div>
  <div><b>How it works.</b> Every day a Claude routine reads everything the sources below published in the last {config["lookback_hours"]} hours, scores each story for relevance, significance and novelty, adjusts for source weight, adds a bonus when several sources cover the same story, and publishes the top {config["top_n"]}. X posts are scored on relevance to serious AI news plus engagement (likes, reposts, views).</div>
  <div style="margin-top:10px"><b>Sources</b> (weight)</div><ul>{src_list}</ul>
  <div style="margin-top:10px"><b>X accounts</b> (weight)</div><ul>{acc_list}</ul>
  <div>Built from <a href="https://github.com/TomsterCZ/ai-news-monitor">TomsterCZ/ai-news-monitor</a>.</div>
</footer>
</div></body></html>"""


def main():
    config = load_json(ROOT / "config.json")
    sources = load_json(ROOT / "sources.json")["sources"]
    accounts = load_json(ROOT / "x_sources.json")["accounts"] if (ROOT / "x_sources.json").exists() else []
    source_weights = {s["name"]: s["weight"] for s in sources}
    account_weights = {a["handle"].lower(): a["weight"] for a in accounts}
    tz = ZoneInfo(config["timezone"])
    files = sorted((ROOT / "data" / "daily").glob("*.json"))
    if not files:
        print("No daily files in data/daily; nothing to build", file=sys.stderr)
        return 1
    days = []
    for f in files:
        day = load_json(f)
        day.setdefault("date", f.stem)
        problems = validate(day, config)
        if problems:
            print(f"{f.name}: INVALID", file=sys.stderr)
            for p in problems:
                print("  - " + p, file=sys.stderr)
            if f == files[-1]:
                return 2
            continue
        enrich(day, config, source_weights, account_weights)
        for it in day["items"]:
            it["published_local"] = fmt_local(it.get("published"), tz)
        for p in day["posts"]:
            p["created_local"] = fmt_local(p.get("created_at"), tz)
        days.append(day)

    all_dates = sorted((d["date"] for d in days), reverse=True)
    (DOCS / "archive").mkdir(parents=True, exist_ok=True)
    (DOCS / "data").mkdir(parents=True, exist_ok=True)
    latest = days[-1]
    for day in days:
        is_latest = day is latest
        (DOCS / "archive" / f"{day['date']}.html").write_text(render_page(day, config, all_dates, sources, accounts, False, tz), encoding="utf-8")
        (DOCS / "data" / f"{day['date']}.json").write_text(json.dumps(day, ensure_ascii=False, indent=1), encoding="utf-8")
        if is_latest:
            (DOCS / "index.html").write_text(render_page(day, config, all_dates, sources, accounts, True, tz), encoding="utf-8")
            (DOCS / "data" / "latest.json").write_text(json.dumps(day, ensure_ascii=False, indent=1), encoding="utf-8")
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Built docs/index.html for {latest['date']} with {len(latest['items'])} items and {len(latest['posts'])} posts; {len(days)} day(s) in archive")
    return 0


if __name__ == "__main__":
    sys.exit(main())

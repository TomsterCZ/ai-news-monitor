#!/usr/bin/env python3
"""Render the website from scored daily files.

Reads  data/daily/*.json  (one file per day, written by the scoring step)
Writes docs/index.html, docs/archive/<date>.html, docs/data/latest.json, docs/data/<date>.json

Usage: python3 pipeline/build.py
Exits non-zero if the newest daily file fails validation.
"""
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
REQUIRED = ("title", "url", "source", "type", "summary", "scores")


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def esc(s):
    return html.escape(str(s or ""), quote=True)


def validate(day, config):
    problems = []
    if not isinstance(day.get("items"), list) or not day["items"]:
        return ["items list is missing or empty"]
    cand_path = ROOT / "data" / "candidates" / f"{day.get('date')}.json"
    known_urls = None
    if cand_path.exists():
        known_urls = {c["url"] for c in load_json(cand_path)["candidates"]}
    for i, it in enumerate(day["items"], 1):
        for key in REQUIRED:
            if key not in it or it[key] in ("", None):
                problems.append(f"item {i}: missing '{key}'")
        if it.get("type") not in config["types"]:
            problems.append(f"item {i}: type '{it.get('type')}' not in config.types")
        sc = it.get("scores") or {}
        for key in ("relevance", "significance", "novelty"):
            v = sc.get(key)
            if not isinstance(v, (int, float)) or not 0 <= v <= 10:
                problems.append(f"item {i}: scores.{key} must be a number 0-10")
        if not str(it.get("url", "")).startswith("http"):
            problems.append(f"item {i}: url must start with http")
        elif known_urls is not None and it["url"] not in known_urls:
            problems.append(f"item {i}: url is not in {cand_path.name}; copy urls verbatim from the candidates file")
    return problems


def final_score(item, config, source_weights):
    w = config["weights"]
    sc = item["scores"]
    base = w["relevance"] * sc["relevance"] + w["significance"] * sc["significance"] + w["novelty"] * sc["novelty"]
    weight = item.get("source_weight") or source_weights.get(item["source"], 1.0)
    extra = min(len(item.get("also_covered_by") or []), config["corroboration_max_sources"])
    score = base + (weight - 1.0) * config.get("source_scale", 4.0) + config["corroboration_bonus"] * extra
    return round(max(0.0, min(10.0, score)), 1), round(base, 2), weight, extra


def enrich(day, config, source_weights):
    for it in day["items"]:
        it["score"], it["score_base"], it["source_weight"], it["corroborations"] = final_score(it, config, source_weights)
    day["items"].sort(key=lambda x: (-x["score"], -x["scores"]["significance"], -x["scores"]["relevance"], -x["scores"]["novelty"]))
    day["items"] = day["items"][: config["top_n"]]
    for rank, it in enumerate(day["items"], 1):
        it["rank"] = rank
    return day


def fmt_local(iso, tz):
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(tz)
        return dt.strftime("%-d %b %Y, %H:%M")
    except ValueError:
        return iso


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--text:#16181d;--muted:#5f6672;--line:#e3e6ea;--accent:#2f6fed;--accent-soft:#e8f0ff;--chip:#eef1f5;--good:#1c8f4e}
@media(prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--text:#e8eaef;--muted:#9aa3b2;--line:#262b35;--accent:#6b9cff;--accent-soft:#1c2740;--chip:#222734;--good:#4cc47f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:860px;margin:0 auto;padding:28px 18px 60px}
header{display:flex;flex-wrap:wrap;gap:12px 20px;align-items:flex-end;justify-content:space-between;margin-bottom:8px}
h1{font-size:30px;margin:0;letter-spacing:-.02em}h1 small{display:block;font-size:14px;font-weight:400;color:var(--muted);margin-top:4px}
.actions{display:flex;gap:8px;flex-wrap:wrap}
.btn{display:inline-block;padding:9px 14px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--text);font-size:14px;font-weight:500}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}.btn:hover{text-decoration:none;filter:brightness(1.05)}
.meta{color:var(--muted);font-size:14px;margin:6px 0 22px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:14px;display:grid;grid-template-columns:52px 1fr;gap:14px}
.rank{font-size:22px;font-weight:700;color:var(--muted);text-align:center;padding-top:2px}
.score{font-size:12px;font-weight:600;color:var(--good);text-align:center;margin-top:4px}
.tags{display:flex;flex-wrap:wrap;gap:6px;font-size:12px;margin-bottom:6px}
.chip{background:var(--chip);border-radius:999px;padding:2px 9px;color:var(--muted)}.chip.type{background:var(--accent-soft);color:var(--accent);font-weight:600;text-transform:capitalize}
h2{font-size:19px;margin:0 0 6px;line-height:1.3}h2 a{color:var(--text)}
.summary{margin:0 0 8px}.why{margin:0 0 8px;color:var(--muted);font-size:14.5px}.why b{color:var(--text);font-weight:600}
details{font-size:13px;color:var(--muted)}summary{cursor:pointer}details table{border-collapse:collapse;margin-top:6px}details td{padding:1px 12px 1px 0}
.also{font-size:13px;color:var(--muted)}
footer{margin-top:36px;padding-top:18px;border-top:1px solid var(--line);color:var(--muted);font-size:13px}
footer ul{columns:2;padding-left:18px;margin:6px 0}@media(max-width:560px){footer ul{columns:1}.card{grid-template-columns:40px 1fr;padding:14px}}
.archive{display:flex;flex-wrap:wrap;gap:8px}.archive a{font-size:13px;padding:4px 10px;border:1px solid var(--line);border-radius:6px;background:var(--card)}
.notice{background:var(--accent-soft);border-radius:8px;padding:10px 14px;font-size:14px;margin-bottom:18px}
"""


def render_item(it):
    sc = it["scores"]
    also = it.get("also_covered_by") or []
    also_html = f'<div class="also">Also covered by: {esc(", ".join(also))}</div>' if also else ""
    why = f'<p class="why"><b>Why it matters:</b> {esc(it["why_it_matters"])}</p>' if it.get("why_it_matters") else ""
    pub = f'<span class="chip">{esc(it["published_local"])}</span>' if it.get("published_local") else ""
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
        <tr><td>Source weight</td><td>{it["source_weight"]} ({"+" if it["source_weight"]>=1 else ""}{round((it["source_weight"]-1)*4.0,1)})</td></tr>
        <tr><td>Corroboration</td><td>+{it["corroborations"]} source(s)</td></tr>
      </table>
    </details>
  </div>
</article>"""


def render_page(day, config, all_dates, sources, is_latest, tz):
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
    notice = "" if is_latest else f'<div class="notice">Archive edition for {esc(date_label)}. <a href="{prefix}index.html">See the latest edition</a>.</div>'
    items_html = "\n".join(render_item(it) for it in day["items"])
    considered = day.get("candidates_considered")
    stats = f' · {considered} candidates from {day.get("sources_ok", "?")} sources' if considered else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · {esc(day["date"])}</title><style>{CSS}</style></head>
<body><div class="wrap">
<header>
  <h1>{esc(title)}<small>Top {len(day["items"])} AI stories for {esc(date_label)}</small></h1>
  <div class="actions">{latest_btn}{refresh_btn}</div>
</header>
<div class="meta">Updated {esc(generated)} ({esc(config["timezone"])}) · last {day.get("lookback_hours", config["lookback_hours"])} hours{stats}</div>
{notice}
{items_html}
<footer>
  <div><b>Archive</b></div><div class="archive" style="margin:8px 0 18px">{archive_links}</div>
  <div><b>How it works.</b> Every day a Claude routine fetches the sources below, reads everything from the last {config["lookback_hours"]} hours, scores each story for relevance, significance and novelty, adjusts for source weight, adds a bonus when several sources cover the same story, and publishes the top {config["top_n"]}.</div>
  <div style="margin-top:10px"><b>Sources</b> (weight)</div><ul>{src_list}</ul>
  <div>Built from <a href="https://github.com/TomsterCZ/ai-news-monitor">TomsterCZ/ai-news-monitor</a>.</div>
</footer>
</div></body></html>"""


def main():
    config = load_json(ROOT / "config.json")
    sources = load_json(ROOT / "sources.json")["sources"]
    source_weights = {s["name"]: s["weight"] for s in sources}
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
        enrich(day, config, source_weights)
        for it in day["items"]:
            it["published_local"] = fmt_local(it.get("published"), tz)
        days.append(day)

    all_dates = sorted((d["date"] for d in days), reverse=True)
    (DOCS / "archive").mkdir(parents=True, exist_ok=True)
    (DOCS / "data").mkdir(parents=True, exist_ok=True)
    latest = days[-1]
    for day in days:
        is_latest = day is latest
        page = render_page(day, config, all_dates, sources, is_latest, tz)
        (DOCS / "archive" / f"{day['date']}.html").write_text(render_page(day, config, all_dates, sources, False, tz), encoding="utf-8")
        (DOCS / "data" / f"{day['date']}.json").write_text(json.dumps(day, ensure_ascii=False, indent=1), encoding="utf-8")
        if is_latest:
            (DOCS / "index.html").write_text(page, encoding="utf-8")
            (DOCS / "data" / "latest.json").write_text(json.dumps(day, ensure_ascii=False, indent=1), encoding="utf-8")
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Built docs/index.html for {latest['date']} with {len(latest['items'])} items; {len(days)} day(s) in archive")
    return 0


if __name__ == "__main__":
    sys.exit(main())

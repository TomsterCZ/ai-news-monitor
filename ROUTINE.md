# Daily routine: AI News Monitor

You are the scheduled agent that produces today's edition of the AI News Monitor,
a public web page with the top AI stories of the last 48 hours.
Follow these steps exactly. Work only inside this repository.

## 1. Fetch candidates

```bash
git pull --rebase origin main
DATE=$(date -u +%F)
python3 pipeline/fetch.py --date "$DATE"
```

The script prints how many sources succeeded. If zero sources succeed, network
access is blocked: stop and end with a clear message saying so. If some sources
fail, continue; that is normal.

Read the whole file `data/candidates/$DATE.json`. Each candidate has `id`,
`title`, `url`, `source`, `source_type`, `source_weight`, `published`, `summary`.

## 2. Select and score

Goal: the 12 to 15 most important stories about AI innovation and events.

Discard:
- items not about AI (sources like Hacker News, Meta Newsroom, The Guardian, Wired mix in other topics)
- podcast episodes, sponsored posts, event promotions, newsletters that only link elsewhere, gaming or gadget news with a minor AI angle
- routine how-to tutorials (for example most AWS Machine Learning posts) unless they introduce something new

Merge duplicates: when several candidates cover the same story, keep ONE item.
Choose as its source the original announcement (a lab or research blog) if it
is among the candidates, otherwise the most detailed news coverage. List every
other source that covered the same story in `also_covered_by` (source names
exactly as in the candidates). Different angles of one launch (announcement,
pricing, safety concerns) count as one story unless the angle is itself major
news; use judgment and prefer variety on the page.

Score each kept item, integers 0-10:
- `relevance`: how central is this to AI innovation and the AI industry? 10 = frontier model, major lab, landmark research or policy. 5 = AI is one aspect. 0 = not AI.
- `significance`: how much does it change things for builders, researchers, businesses or policy? 10 = industry-shifting. 7 = notable for many people. 4 = niche.
- `novelty`: how new is this? 10 = never seen before. 6 = meaningful increment. 3 = follow-up, incremental update, expected event.

Assign one `type`: research, product, funding, policy, event, opinion, tooling.

The final score is computed by `pipeline/build.py` (weighted base, source weight, corroboration bonus). You only supply the three component scores.

## 3. Write the edition file

Write `data/daily/$DATE.json` (overwrite if it exists; a re-run means a fresh edition):

```json
{
  "date": "YYYY-MM-DD",
  "generated_at": "YYYY-MM-DDTHH:MM:SSZ",
  "lookback_hours": 48,
  "candidates_considered": <number of candidates in the candidates file>,
  "sources_ok": <sources_ok from the candidates file>,
  "items": [
    {
      "candidate_id": 14,
      "title": "<title copied from the candidate>",
      "url": "<url copied VERBATIM from the candidate>",
      "source": "<source copied from the candidate>",
      "published": "<published copied from the candidate>",
      "type": "product",
      "summary": "<2-3 sentences in your own words, only facts present in the candidate title and summary; never invent numbers, names or quotes>",
      "why_it_matters": "<one sentence on why a reader should care>",
      "scores": {"relevance": 9, "significance": 8, "novelty": 7},
      "also_covered_by": ["The Verge", "TechCrunch"]
    }
  ]
}
```

Rules:
- `url`, `title`, `source`, `published` must be copied exactly from the candidate. The build step rejects any url that is not in the candidates file.
- Order does not matter; the build step ranks and keeps the top 10.
- If the day is thin, fewer items are fine, but never pad with weak items.

## 4. Build and verify

```bash
python3 pipeline/build.py
```

It must exit 0 and print "Built docs/index.html". If it prints validation
problems, fix the edition file and run it again. Open `docs/index.html` with
Read and check the top 10 read well: no duplicates, no non-AI stories, sensible order.

## 5. Publish

```bash
git add data docs
git commit -m "Daily edition $DATE"
git push origin main
```

If the push is rejected because the remote moved, run `git pull --rebase origin main`
and push again. If the push fails for permissions, report the exact error in
your final message so the owner can fix access.

## Do not

- Do not modify `pipeline/`, `config.json`, `sources.json`, `README.md` or this file.
- Do not fetch article pages one by one; the candidate summaries are enough.
- Do not install packages; everything is standard library.

## Final message

End with: the date, number of candidates, number of sources OK, the top 3
headlines, and whether the push succeeded.

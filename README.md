# CCASS Dashboard — GitHub Pages

A DisclosureTracker-style dashboard for **one HK stock at a time**:

1. **Interactive price chart** — candlestick + volume (Lightweight Charts), 30 / 90 / 180 days.
2. **Top 10 CCASS holders chart** — holdings over time, toggle brokers on/off (Chart.js).
3. **Daily CCASS change table** — *all* participants, sortable, searchable, change vs previous trading day.

Demo stock: **2501 Maiyue Technology Limited** (Webb-site CCASS `i=34573`, confirmed by the user 2026-11-07). Price data in `data/` is real (Yahoo Finance, 247 daily bars to 2026-11-06, close HK$1.71). CCASS data for 2501 in `data/ccass_*_2501.json` is **real HKEX SDW data** for 30 trading days, 2026-09-28 → 2026-11-06 (108 participants ever, 95 on Nov 6; 170,469,990 shares = 30.5% of 558,838,000 issued; top holder Zheshang International 28,022,000 = 5.01%). Over that window total CCASS rose 165.60M → 170.47M (+4.87M); biggest accumulators HSBC +3.04M, Futu +2.90M, Citibank +2.45M; biggest reducer Valuable Capital -4.46M (-26.9%). CCASS data in `data/*_sample.json` (9988) is **synthetic sample data, clearly labelled** — format example only.

## How data gets here (the important part)

GitHub Pages is static — it cannot scrape HKEX or call Yahoo from the browser (CORS, and HKEX has no public JSON API for CCASS). So the architecture is:

```
HKEX SDW search (form POST) ─┐
Yahoo Finance chart API      ├─► scripts/*.py (or GitHub Action, daily)
                             │    writes data/*.json, committed to git
GitHub Pages ────────────────┘    index.html reads only those JSON files
```

- **Price:** `scripts/fetch_price.py 9988` → `data/price_9988.json`
  Yahoo v8 chart API, no key. Verified 2026-11-07 for 9988.HK / 0700.HK.
- **CCASS:** `scripts/fetch_ccass.py 9988 [--backfill 30]` → `data/ccass_history_9988.json` + `data/ccass_latest_9988.json`
  Scrapes the HKEX ASP.NET form at `https://www3.hkexnews.hk/sdw/search/searchsdw.aspx`
  (GET `__VIEWSTATE`, POST `txtShareholdingDate` + `txtStockCode`, parse the result table).
  HKEX keeps only the past 12 months online, so the script appends each day to the JSON history in git — that history is what powers the Top-10 chart.
- **Daily update:** `.github/workflows/daily.yml` runs Mon–Fri 06:30 HKT, fetches both, commits `data/`.

## Deploy to GitHub

2501 is already backfilled — price + 30 trading days of CCASS (2026-09-28 → 2026-11-06) are in `data/`. You do **not** need to run the scrapers again to go live; the daily Action takes over from here.

```bash
# 1. Create a repo on GitHub (see walkthrough in chat), then push this folder
git init && git add . && git commit -m "CCASS dashboard - 2501 Maiyue" && git push

# 2. GitHub repo → Settings → Pages → Deploy from branch → main, /(root)
#    Site will be at https://<you>.github.io/<repo>/
```

To add another stock: change the code in the top box (it loads `data/price_<code>.json`), or run the two scripts for that code and add it to `config.json`.

## What I verified (2026-11-07)

- ✅ DisclosureTracker landing: it is a licensed HKEX data vendor — CCASS Shareholding Display Licence + delayed market data vendor. Its CCASS charts are the model for the Top-10 chart here.
- ✅ Yahoo price API works from a server, for HK stocks.
- ✅ HKEX SDW form loads (GET works, fields `txtShareholdingDate`, `txtStockCode`, `btnSearch`, `__VIEWSTATE` confirmed).
- ✅ HKEX search **POST works for 2501** — the 30-day backfill completed 2026-11-07 (~28s per day; one GET timed out mid-run, so the scraper now retries the GET 3× and saves progress after each day). If a future run returns no rows, it saves `data/_last_hkex_debug.html` — re-inspect the form field names.
- ✅ Webb-site `i=34573` = **HK 2501 Maiyue Technology Limited**, confirmed by the user 2026-11-07. The Webb-site mirror itself was still returning 403 that day, so HKEX remains the primary feed.

## Licence / terms note

HKEX CCASS data is HKEX data. This repo is built for personal research: attribute HKEX as the source, keep the fetch polite (once per stock per day), and check HKEX's CCASS display terms before publishing the data publicly at scale — DisclosureTracker holds a specific HKEX licence for that. The code here is yours; the data is not.

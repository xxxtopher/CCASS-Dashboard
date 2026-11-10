#!/usr/bin/env python3
"""Fetch the last 30 days of HKEX announcements for a stock, via the
HKEX Listed Company Information title search
(https://www1.hkexnews.hk/search/titlesearch.xhtml), and save
data/news_<code>.json for the dashboard's news column.

How the search works (reverse-engineered 2026-11-09):
- The stock picker resolves a code to HKEX's internal stockId through
  /search/partial.do?lang=EN&type=A&name=<code>&market=SEHK&callback=...
  (2501 -> 1000203640, 2497 -> 1000208368). Those IDs are hard-coded below
  and only looked up live for codes not in the map.
- The search is a POST of the TitleSearchPanel form to
  /search/titlesearch.xhtml?lang=en with: lang=EN, market=SEHK,
  searchType=1 (headline categories after 2006), stockId, from/to as
  yyyymmdd, category=0 (Current Securities), documentType=-1 (All),
  t1code=t2code=t2Gcode=-2 (all headline categories), title=.
  NOTE: category=0 is essential — with it empty the search returns 0 rows.
- Results come back server-rendered in the same page: a table with
  Release Time | Stock Code | Stock Short Name | Document, where the
  Document cell holds the headline category, the title as a link to the
  announcement PDF (/listedco/listconews/sehk/...), and the file size.
- The date picker caps To at today, so the window is today-30d .. today
  (HKT). A single stock may be searched up to 12 months back, so 30 days
  is well within the limit.

Usage: python3 scripts/fetch_news.py <code> [code ...]
Writes data/news_<code>.json. Exits non-zero if a stock's fetch fails
after retries (the GitHub Action tolerates this per stock).
"""
import datetime
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://www1.hkexnews.hk"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# HTTP goes through curl, not urllib: on 2026-11-09 www1.hkexnews.hk
# intermittently closed urllib connections mid-request ("Remote end closed
# connection without response", 1 of 2 GETs failing) while curl was 3/3
# stable against the same host. curl ships on ubuntu-latest, so the
# GitHub Action can use this script unchanged.

# HKEX internal stockIds, verified via partial.do on 2026-11-09.
STOCK_IDS = {"2501": 1000203640, "2497": 1000208368}
WINDOW_DAYS = 30


def curl_text(url, jar, data=None, referer=None, timeout=60):
    """GET (data None) or POST (data dict) via curl; returns body text."""
    cmd = ["curl", "-sS", "-A", UA, "-c", str(jar), "-b", str(jar),
           "--max-time", str(timeout)]
    if referer:
        cmd += ["-H", f"Referer: {referer}"]
    if data is not None:
        cmd += ["-X", "POST"]
        for k, v in data.items():
            cmd += ["--data-urlencode", f"{k}={v}"]
    cmd += [url]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
    if r.returncode != 0:
        raise RuntimeError(f"curl {url} failed (rc={r.returncode}): "
                           f"{r.stderr.strip()[:200]}")
    return r.stdout


def resolve_stock_id(jar, code5):
    """Look up HKEX's internal stockId for a 5-digit code via partial.do."""
    url = (f"{BASE}/search/partial.do?lang=EN&type=A&name={code5}"
           f"&market=SEHK&callback=callback")
    body = curl_text(url, jar, timeout=30)
    m = re.search(r'"stockId":(\d+),"code":"' + code5 + r'"', body)
    if not m:
        raise RuntimeError(f"partial.do returned no stockId for {code5}: {body[:200]}")
    return int(m.group(1))


def parse_results(html):
    """Return [{date, time, category, title, url, size}] newest first."""
    m = re.search(r'<table class="table sticky-header-table.*?</table>', html, re.S)
    if not m:
        raise RuntimeError("results table not found in search response")
    items = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(0), re.S):
        # Release time cell reads "Release Time: DD/MM/YYYY HH:MM" — the
        # mobile-list-heading label prefixes the value, so search the row
        # rather than anchoring at the cell start.
        when = re.search(r"(\d{2})/(\d{2})/(\d{4})(?:\s+(\d{2}):(\d{2}))?", row)
        link = re.search(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', row, re.S)
        if not when or not link:
            continue
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", link.group(2))).strip()
        cat = re.search(r'<div class="headline">(.*?)</div>', row, re.S)
        category = (re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", cat.group(1))).strip()
                    if cat else "")
        size = re.search(r'attachment_filesize">([^<]+)<', row)
        dd, mm, yyyy = int(when.group(1)), int(when.group(2)), int(when.group(3))
        hh, mi = (int(when.group(4)), int(when.group(5))) if when.group(4) else (0, 0)
        items.append({
            "date": f"{yyyy:04d}-{mm:02d}-{dd:02d}",
            "time": f"{hh:02d}:{mi:02d}",
            "category": category,
            "title": title,
            "url": urllib.parse.urljoin(BASE + "/", link.group(1)),
            "size": size.group(1) if size else None,
        })
    # Newest first (the server already returns latest first; be safe).
    items.sort(key=lambda x: (x["date"], x["time"]), reverse=True)
    return items


def fetch_news(jar, code5, stock_id, date_from, date_to):
    data = {
        "lang": "EN", "market": "SEHK", "searchType": "1",
        "stockId": str(stock_id),
        "from": date_from.strftime("%Y%m%d"),
        "to": date_to.strftime("%Y%m%d"),
        "category": "0", "documentType": "-1",
        "t1code": "-2", "t2code": "-2", "t2Gcode": "-2", "title": "",
    }
    last_err = None
    for attempt in range(3):
        try:
            html = curl_text(BASE + "/search/titlesearch.xhtml?lang=en", jar,
                             data=data,
                             referer=BASE + "/search/titlesearch.xhtml?lang=en")
            return parse_results(html)
        except Exception as e:
            last_err = e
            print(f"  news POST retry {attempt + 1}/3: {e}", flush=True)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"news search failed for {code5}: {last_err}")


def main():
    codes = sys.argv[1:]
    if not codes:
        print("usage: fetch_news.py <code> [code ...]", file=sys.stderr)
        sys.exit(2)
    hkt = datetime.timezone(datetime.timedelta(hours=8))
    today = datetime.datetime.now(hkt).date()
    date_to = today
    date_from = today - datetime.timedelta(days=WINDOW_DAYS)
    # Establish the session (cookies) the way a browser load does.
    with tempfile.TemporaryDirectory() as td:
        jar = Path(td) / "cookies.txt"
        for attempt in range(3):
            try:
                curl_text(BASE + "/search/titlesearch.xhtml?lang=en", jar,
                          timeout=30)
                break
            except Exception as e:
                print(f"  news session GET retry {attempt + 1}/3: {e}", flush=True)
                time.sleep(5 * (attempt + 1))
        else:
            print("  FAILED: could not establish HKEX session", file=sys.stderr)
            sys.exit(1)
        ok = True
        for code in codes:
            short = str(int(code))
            code5 = code.zfill(5)
            stock_id = STOCK_IDS.get(short)
            try:
                if stock_id is None:
                    stock_id = resolve_stock_id(jar, code5)
                    print(f"  resolved stockId for {code5}: {stock_id}")
                print(f"Fetching news {code5} {date_from} -> {date_to} ...", flush=True)
                items = fetch_news(jar, code5, stock_id, date_from, date_to)
                out = {
                    "stock_code": short,
                    "source": "HKEX Listed Company Information title search "
                              "(www1.hkexnews.hk/search/titlesearch.xhtml)",
                    "from": date_from.isoformat(), "to": date_to.isoformat(),
                    "fetched_at": datetime.datetime.now(datetime.timezone.utc)
                                  .isoformat(timespec="seconds"),
                    "count": len(items), "items": items,
                }
                (ROOT / "data" / f"news_{short}.json").write_text(
                    json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
                print(f"  saved news_{short}.json: {len(items)} announcements",
                      flush=True)
            except Exception as e:
                print(f"  FAILED news {code5}: {e}", file=sys.stderr, flush=True)
                ok = False
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

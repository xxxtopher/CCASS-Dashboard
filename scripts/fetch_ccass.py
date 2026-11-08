#!/usr/bin/env python3
"""
Fetch CCASS holdings for one HK stock from HKEX Shareholding Search
  https://www3.hkexnews.hk/sdw/search/searchsdw.aspx
and write:
  data/ccass_history_<code>.json   (dates x participants, for the Top-10 chart)
  data/ccass_latest_<code>.json    (all participants, daily change vs previous day)

How it works: the page is an ASP.NET form. GET it, take __VIEWSTATE /
__VIEWSTATEGENERATOR, then POST txtShareholdingDate + txtStockCode with
btnSearch=Search, and parse the result table:
  Participant ID | Participant Name | Shareholding | % of total issued shares

HKEX only keeps 12 months online. Run once per trading day after the
close (CCASS is updated overnight) and keep the JSON history in git, so
the chart builds up over time. Do NOT hammer it: one request per stock
per day, with retries + a polite delay.

Usage:
  python3 scripts/fetch_ccass.py 9988            # today (HKT)
  python3 scripts/fetch_ccass.py 9988 2026-11-06
  python3 scripts/fetch_ccass.py 9988 2026-11-06 --backfill 30   # last 30 trading days

Notes / limits (verified 2026-11-07):
- GET of the form works; the POST timed out on Sat 7 Nov 2026 during
  HKEX scheduled maintenance (09:00-21:30 HKT). If it times out, retry
  after maintenance -- the code below retries 3x with backoff.
- There is NO official HKEX JSON API for this page. If HKEX changes the
  form field names, re-inspect the GET HTML (fields are listed in the
  code's debug print) and update FORM_FIELDS.
- Webb-site (webb-site.com/ccass/choldings?i=<org-id>) is a useful
  cross-check, but its original site now redirects and its 0xmd mirror
  returned 403 on 2026-11-07, so it is NOT used as the primary feed here.
"""
import argparse, datetime, json, re, sys, time, urllib.parse, urllib.request, http.cookiejar
from pathlib import Path

BASE = "https://www3.hkexnews.hk/sdw/search/searchsdw.aspx"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
ROOT = Path(__file__).resolve().parent.parent

def field(html, name):
    m = re.search(r'name="%s"[^>]*value="([^"]*)"' % re.escape(name), html)
    return m.group(1) if m else ""

def fetch_day(opener, code5, date_str):
    """Return (rows, issued_shares) for one date."""
    # HKEX is slow/flaky during/after maintenance (GET took 9s, POST ~28s
    # on 2026-11-07); retry the GET as well as the POST.
    html = None
    for attempt in range(3):
        try:
            with opener.open(urllib.request.Request(BASE, headers=UA), timeout=60) as r:
                html = r.read().decode("utf-8", errors="ignore")
            break
        except Exception as e:
            print(f"  GET retry {attempt+1}/3: {e}", flush=True)
            time.sleep(5 * (attempt + 1))
    if html is None:
        raise RuntimeError(f"HKEX GET failed after 3 tries for {code5} on {date_str}")
    data = {
        "__EVENTTARGET": "", "__EVENTARGUMENT": "",
        "__VIEWSTATE": field(html, "__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": field(html, "__VIEWSTATEGENERATOR"),
        "today": field(html, "today"),
        "sortBy": "", "sortDirection": "", "alertMsg": "",
        "txtShareholdingDate": date_str.replace("-", "/"),
        "txtStockCode": code5, "txtStockName": "",
        "txtParticipantID": "", "txtParticipantName": "", "txtSelPartID": "",
        "btnSearch": "Search",
    }
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(BASE, data=body, headers={**UA,
        "Content-Type": "application/x-www-form-urlencoded", "Referer": BASE})
    last_err = None
    for attempt in range(3):
        try:
            with opener.open(req, timeout=60) as r:
                html2 = r.read().decode("utf-8", errors="ignore")
            break
        except Exception as e:
            last_err = e; time.sleep(5 * (attempt + 1))
    else:
        raise RuntimeError(f"HKEX POST failed after 3 tries: {last_err}")
    rows = []
    # Each data row has 5 <td class="col-..."> cells, each with a
    # <div class="mobile-list-body">VALUE</div>:
    #   [participant ID, participant name, address, shareholding, % issued]
    # Verified 2026-11-07 for 2501 on 2026-11-06 (96 <tr>s incl. headers).
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html2, re.S):
        bodies = [re.sub(r"\s+", " ", b).strip()
                  for b in re.findall(r'<div class="mobile-list-body">(.*?)</div>', row, re.S)]
        if len(bodies) >= 5 and re.match(r"^[A-Z]\d{5}$", bodies[0] or ""):
            try:
                rows.append({
                    "participant_id": bodies[0],
                    "name": bodies[1],
                    "holding": int(bodies[3].replace(",", "")),
                    "pct_issued": float(bodies[4].replace("%", "")),
                })
            except ValueError:
                continue
    # Total issued shares, for % cross-checks (shown on the summary line)
    flat = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html2))
    m_issued = re.search(r"Total number of Issued Shares/Warrants/Units \(last updated figure\)\s*([\d,]+)", flat)
    issued = int(m_issued.group(1).replace(",", "")) if m_issued else None
    if not rows:
        # Save a debug snippet so a form change is diagnosable, without spamming the repo
        (ROOT / "data" / "_last_hkex_debug.html").write_text(html2[:20000], encoding="utf-8")
        raise RuntimeError(f"No CCASS rows parsed for {code5} on {date_str} "
                           f"(HKEX may be in maintenance, or the form changed; see data/_last_hkex_debug.html)")
    return rows, issued

# Known corporate actions that change total issued shares (verified):
# 2501 placed 58,838,000 new shares, completed 2026-09-07 — issued was
# 500,000,000 before that date and 558,838,000 from it (HKEX announcement
# + Sep 2026 Monthly Return). HKEX SDW only shows the *current* issued
# figure, so without this a backfill over the placing would stamp the new
# figure onto pre-placing dates and understate their % owned.
# Format: code -> [(effective_date or None, issued_shares), ...] sorted.
ISSUED_EVENTS = {"2501": [(None, 500000000), ("2026-09-07", 558838000)]}
MAX_HISTORY_DAYS = 90  # keep at most 90 trading days of CCASS history

def issued_for_date(code, ds, page_issued):
    events = ISSUED_EVENTS.get(code)
    if not events:
        return page_issued
    value = None
    for eff, val in events:
        if eff is None or ds >= eff:
            value = val
    return value if value is not None else page_issued

def trading_days_back(end, n):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= datetime.timedelta(days=1)
    return sorted(out)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("code", help="HK stock code, e.g. 9988")
    ap.add_argument("date", nargs="?", help="YYYY-MM-DD, default today HKT")
    ap.add_argument("--backfill", type=int, default=1)
    args = ap.parse_args()
    code5 = args.code.zfill(5)
    hkt = datetime.timezone(datetime.timedelta(hours=8))
    end = (datetime.date.fromisoformat(args.date) if args.date
           else datetime.datetime.now(hkt).date())
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

    # history keyed by short code to match the frontend (price_9988.json etc.)
    short = str(int(code5))
    hist_path = ROOT / "data" / f"ccass_history_{short}.json"
    latest_path = ROOT / "data" / f"ccass_latest_{short}.json"
    hist = json.loads(hist_path.read_text()) if hist_path.exists() else {
        "stock_code": short, "issued_shares": None,
        "source": "HKEX CCASS Shareholding Search (www3.hkexnews.hk/sdw/search/searchsdw.aspx)",
        "dates": [], "participants": []}
    # Remove sample marker if a real run replaces it
    hist.pop("source", None)
    hist["source"] = "HKEX CCASS Shareholding Search (www3.hkexnews.hk/sdw/search/searchsdw.aspx)"

    days = trading_days_back(end, args.backfill)
    by_id = {p["participant_id"]: p for p in hist["participants"]}
    latest_pct = {}  # HKEX gives % of issued shares directly per row; keep today's
    for d in days:
        ds = d.isoformat()
        if ds in hist["dates"]:
            continue
        print(f"Fetching {code5} {ds} ...", flush=True)
        rows, issued = fetch_day(opener, code5, ds)
        # Record issued shares PER DATE: the denominator for that date's
        # % owned. Total issued only changes on a corporate action
        # (placing, buyback, ...); shares moving between CCASS and
        # non-CCASS holders change the CCASS / Not-in-CCASS split, not
        # the issued total. Keeping it per-date means a future placing
        # won't rewrite the % owned on past dates.
        issued = issued_for_date(short, ds, issued)
        if issued:
            hist["issued_shares"] = issued
            hist.setdefault("issued_by_date", {})[ds] = issued
        latest_pct = {r["participant_id"]: r["pct_issued"] for r in rows}
        # Insert this date at its sorted position (not just appended):
        # backfills fetch older dates after newer ones, and holdings[i]
        # must always line up with dates[i]. This bug once shipped a
        # history file whose holdings were in fetch order, not date order.
        idx = sum(1 for x in hist["dates"] if x < ds)
        hist["dates"].insert(idx, ds)
        for r in rows:
            p = by_id.setdefault(r["participant_id"],
                                 {"participant_id": r["participant_id"], "name": r["name"], "holdings": []})
            p["holdings"] += [None] * (idx - len(p["holdings"]))
            p["holdings"].insert(idx, r["holding"])
        # Participants NOT holding on this date still need a None AT idx —
        # appending it at the end (the old code) silently shifted every
        # later holding one slot left, so Nov 6 totals came out ~28M
        # shares light after a July backfill. This bug only bites when a
        # date is inserted before existing ones (backfill); appending the
        # latest day is unaffected.
        today_ids = {r["participant_id"] for r in rows}
        for p in by_id.values():
            if p["participant_id"] not in today_ids:
                p["holdings"] += [None] * (idx - len(p["holdings"]))
                p["holdings"].insert(idx, None)
            p["holdings"] += [None] * (len(hist["dates"]) - len(p["holdings"]))
        # Save progress after every day, so a later HKEX timeout
        # does not lose the days already fetched.
        hist["participants"] = list(by_id.values())
        hist_path.write_text(json.dumps(hist, indent=2), encoding="utf-8")
        print(f"  saved {ds}: {len(rows)} participants", flush=True)
        time.sleep(2)  # polite pacing

    # Cap history at MAX_HISTORY_DAYS trading days: oldest dates drop off
    # the front (holdings arrays and issued_by_date trimmed with them), so
    # the file — and the charts — stay fast as the daily run adds days.
    if len(hist["dates"]) > MAX_HISTORY_DAYS:
        drop = len(hist["dates"]) - MAX_HISTORY_DAYS
        hist["dates"] = hist["dates"][drop:]
        for p in hist["participants"]:
            p["holdings"] = p["holdings"][drop:]
        if "issued_by_date" in hist:
            hist["issued_by_date"] = {d: hist["issued_by_date"][d]
                                      for d in hist["dates"] if d in hist["issued_by_date"]}
        hist_path.write_text(json.dumps(hist, indent=2), encoding="utf-8")
        print(f"  trimmed history to latest {MAX_HISTORY_DAYS} trading days", flush=True)

    # Latest-day table: today vs previous date in history
    if len(hist["dates"]) >= 2:
        today, prev = hist["dates"][-1], hist["dates"][-2]
        ti, pi = hist["dates"].index(today), hist["dates"].index(prev)
        rows = []
        for p in hist["participants"]:
            h, ph = p["holdings"][ti], p["holdings"][pi]
            if h is None:
                continue
            rows.append({"participant_id": p["participant_id"], "name": p["name"],
                         "holding": h,
                         "pct_issued": latest_pct.get(p["participant_id"]),
                         "change": (h - ph) if ph is not None else None,
                         "change_pct": round((h - ph) / ph * 100, 3) if ph else None})
        rows.sort(key=lambda x: -x["holding"])
        latest_path.write_text(json.dumps({
            "stock_code": short, "date": today, "prev_date": prev,
            "source": hist["source"], "issued_shares": hist.get("issued_shares"),
            "rows": rows}, indent=2), encoding="utf-8")
        print(f"Wrote {hist_path.name} ({len(hist['dates'])} dates, {len(hist['participants'])} participants) and {latest_path.name}")
    else:
        print(f"Wrote {hist_path.name}; need 2 dates before a daily-change table can be built.")

if __name__ == "__main__":
    main()

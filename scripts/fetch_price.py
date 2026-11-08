#!/usr/bin/env python3
"""Fetch daily OHLCV for a HK stock from Yahoo Finance (no key needed)
and save data/price_<code>.json for the dashboard.

  python3 scripts/fetch_price.py 9988 [0700 ...]

Yahoo's v8 chart endpoint is free but blocks browsers via CORS, so the
dashboard must NOT call it from the page -- this script (run by GitHub
Actions daily) fetches it server-side and commits the JSON instead.
Verified 2026-11-07: 9988.HK and 0700.HK both returned 6 months of
daily candles, last bar 2026-11-06.
"""
import datetime, json, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def fetch(sym, rng="1y"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.load(r)
    res = d["chart"]["result"][0]
    meta, ts = res["meta"], res["timestamp"]
    q = res["indicators"]["quote"][0]
    candles = [{"date": datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%d"),
                "open": q["open"][i], "high": q["high"][i], "low": q["low"][i],
                "close": q["close"][i], "volume": q["volume"][i]}
               for i, t in enumerate(ts) if q["close"][i] is not None]
    return {"symbol": sym, "name": meta.get("longName"), "currency": meta.get("currency"),
            "fetched_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "source": "Yahoo Finance chart API (query1.finance.yahoo.com/v8/finance/chart)",
            "candles": candles}

if __name__ == "__main__":
    codes = sys.argv[1:] or ["9988"]
    for code in codes:
        short = str(int(code))
        data = fetch(f"{short}.HK")
        p = ROOT / "data" / f"price_{short}.json"
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(p.name, data["name"], len(data["candles"]), "bars, last:", data["candles"][-1])

"""
GovDeals source.

Two jobs, kept deliberately separate:

  discover_assets()  -- cheap, verified. Pulls the public sitemap GovDeals
                        publishes for crawlers and filters it to the seller(s)
                        you care about. No browser, no auth, just an XML file.

  fetch_details()    -- loads each watched listing in a real headless browser,
                        the same way a visitor would, and reads what rendered.
                        We do NOT call the private maestro/authservice API
                        directly (that's their "registered clients only" gate);
                        we let the page's own frontend authenticate itself and
                        capture the JSON it fetches, falling back to scraping
                        the rendered text.

robots.txt for govdeals.com allows /en/asset/ and asks for a 5s crawl-delay,
which is why delay_seconds defaults to 6.
"""
import os
import re
import json
import asyncio
from datetime import datetime, timezone

import requests
from dateutil import parser as dateparse

SITEMAP_URL = "https://files.lqdt1.com/zstcntr-sitemap/Assets_Sitemap_GovDeals.xml"
SM_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
ASSET_RE = re.compile(r"/en/asset/(\d+)/(\d+)")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# tz abbreviations GovDeals shows in close times -> UTC offset (hours)
TZ_OFFSETS = {"PST": -8, "PDT": -7, "MST": -7, "MDT": -6,
              "CST": -6, "CDT": -5, "EST": -5, "EDT": -4, "UTC": 0, "GMT": 0}


class AccessDenied(RuntimeError):
    pass


# ---------------------------------------------------------------- discovery
# GovDeals' sitemap declares encoding="utf-16" but actually serves UTF-8, which
# breaks strict XML parsers. We don't need real XML parsing - just the canonical
# <loc> URLs - so we regex over the decoded text and skip the declaration trap.
LOC_RE = re.compile(r"<loc>(https://www\.govdeals\.com/en/asset/(\d+)/(\d+))</loc>")


def discover_assets(sellers):
    """Yield (asset_id, seller_id, url) for the given seller IDs.

    sellers may be ints or strings. Downloads the ~19MB sitemap once and scans
    it; peak memory is ~40MB, fine for any machine.
    """
    sellers = {str(s) for s in sellers}
    resp = requests.get(SITEMAP_URL, headers={"User-Agent": UA}, timeout=120)
    resp.raise_for_status()
    data = resp.content
    text = None
    for enc in ("utf-8", "utf-16"):           # try the real encodings, not the declared one
        try:
            cand = data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        if "/en/asset/" in cand:
            text = cand
            break
    if text is None:
        text = data.decode("utf-8", errors="replace")

    for m in LOC_RE.finditer(text):
        url, asset_id, seller_id = m.group(1), m.group(2), m.group(3)
        if seller_id in sellers:
            yield asset_id, seller_id, url


# ------------------------------------------------------------ detail helpers
def _walk(obj, keys):
    """Depth-first search of nested dict/list for the first value under any key."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k.lower() in keys and v not in (None, "", []):
                    return v
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


def _to_utc(value):
    """Accept ISO string or epoch (s/ms) -> ISO8601 UTC, or None."""
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            ts = value / 1000 if value > 1e12 else value
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        dt = dateparse.parse(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        return None


def parse_close_text(text):
    """Pull an absolute close time like 'Jul 03, 2026 09:10 AM MDT' from page text."""
    m = re.search(r"([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4}\s+\d{1,2}:\d{2}\s*[AP]M)\s*([A-Z]{2,4})", text)
    if not m:
        return None
    naive = dateparse.parse(m.group(1))
    off = TZ_OFFSETS.get(m.group(2).upper())
    if off is None:
        return naive.replace(tzinfo=timezone.utc).isoformat()
    # local time in tz with offset `off` -> UTC
    from datetime import timedelta
    return (naive - timedelta(hours=off)).replace(tzinfo=timezone.utc).isoformat()


def parse_from_text(text):
    """Best-effort scrape from rendered visible text."""
    reserve = None
    low = text.lower()
    if "reserve not met" in low:
        reserve = 0
    elif "reserve met" in low or "reserve has been met" in low:
        reserve = 1

    price, currency = None, "USD"
    # prefer an amount that follows a price-ish label
    labels = r"(?:current bid|current price|winning bid|high bid|sale price|price)"
    lab = re.search(labels + r"[^\$]{0,40}\$\s?([\d,]+(?:\.\d{2})?)", text, re.I)
    if lab:
        price = float(lab.group(1).replace(",", ""))
    else:
        amts = re.findall(r"\$\s?([\d,]+(?:\.\d{2})?)", text)
        if amts:
            price = max(float(a.replace(",", "")) for a in amts)
    return price, currency, reserve, parse_close_text(text)


def _from_json(blobs):
    """Map any captured API JSON to our fields. Returns dict (may be partial)."""
    out = {}
    for _, j in blobs:
        if out.get("title") and out.get("current_price") and out.get("close_at"):
            break
        out.setdefault("title", _walk(j, {"title", "name", "assetname",
                                           "productname", "shortdescription"}))
        out.setdefault("description", _walk(j, {"description", "longdescription"}))
        p = _walk(j, {"currentprice", "currentbid", "currentamount", "highbid",
                      "price", "saleprice", "currentbidamount", "currentwinningbid"})
        if p is not None and "current_price" not in out:
            try:
                out["current_price"] = float(p)
            except (TypeError, ValueError):
                pass
        out.setdefault("currency", _walk(j, {"currency", "currencycode"}) or "USD")
        c = _walk(j, {"closedatetime", "enddatetime", "saleenddate", "closedate",
                      "enddate", "auctionclosedate", "biddingendsat"})
        if c and "close_at" not in out:
            iso = _to_utc(c)
            if iso:
                out["close_at"] = iso
        rv = _walk(j, {"reservemet", "isreservemet", "reservestatus"})
        if rv is not None and "reserve_met" not in out:
            if isinstance(rv, bool):
                out["reserve_met"] = int(rv)
            elif isinstance(rv, str):
                out["reserve_met"] = 0 if "not" in rv.lower() else 1
    return {k: v for k, v in out.items() if v is not None}


async def _fetch_one(page, url, timeout_ms, dump_dir=None):
    blobs = []

    async def on_resp(resp):
        u = resp.url
        if ("maestro" in u or "liveupdates" in u) and resp.request.resource_type in ("xhr", "fetch"):
            try:
                blobs.append((u, await resp.json()))
            except Exception:
                pass

    page.on("response", lambda r: asyncio.ensure_future(on_resp(r)))
    try:
        await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
    except Exception:
        pass  # networkidle can time out on chatty SPAs; we still read what's there
    await page.wait_for_timeout(3500)

    title = await page.title()
    body = await page.inner_text("body")
    if "access denied" in body.lower() and len(body) < 600:
        raise AccessDenied(url)

    if dump_dir:
        os.makedirs(dump_dir, exist_ok=True)
        aid = ASSET_RE.search(url).group(1)
        with open(os.path.join(dump_dir, f"{aid}.txt"), "w") as f:
            f.write(body)
        with open(os.path.join(dump_dir, f"{aid}.json"), "w") as f:
            json.dump(blobs, f, indent=2, default=str)

    data = _from_json(blobs)                       # API-first
    p, cur, res, close = parse_from_text(body)     # DOM fallback fills gaps
    data.setdefault("current_price", p)
    data.setdefault("currency", cur)
    if "reserve_met" not in data and res is not None:
        data["reserve_met"] = res
    data.setdefault("close_at", close)
    if not data.get("title") and title and title.lower() not in ("home", "access denied"):
        data["title"] = title
    data["status"] = "active"
    return data


async def _fetch_all(urls, cfg, dump):
    from playwright.async_api import async_playwright
    delay = cfg["crawl"]["delay_seconds"]
    timeout = cfg["crawl"]["detail_timeout_ms"]
    dump_dir = "dumps" if dump else None
    results = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=cfg["crawl"]["headless"],
                                           args=["--no-sandbox"])
        ctx = await browser.new_context(user_agent=UA, locale="en-US")
        page = await ctx.new_page()
        for i, url in enumerate(urls):
            if i:
                await page.wait_for_timeout(delay * 1000)
            try:
                results[url] = await _fetch_one(page, url, timeout, dump_dir)
            except AccessDenied:
                raise
            except Exception as e:
                results[url] = {"error": str(e)}
        await browser.close()
    return results


def fetch_details(urls, cfg, dump=False):
    """Sync wrapper. Returns {url: details_dict}."""
    if not urls:
        return {}
    return asyncio.run(_fetch_all(urls, cfg, dump))

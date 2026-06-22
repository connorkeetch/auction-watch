# auction-watch

Watches [GovDeals](https://www.govdeals.com) for a specific seller's listings,
estimates rough resale value, and emails you when something **closing within a
day** is **priced below a threshold**. Built for personal deal-hunting (the
seller behind the Salt Lake City lab gear is `7735`).

## How it gets data (and what it deliberately doesn't do)

GovDeals listing pages are a JavaScript app; the real data comes from an
internal API (`maestro.lqdt1.com`, behind an OAuth gateway) that their own docs
restrict to **registered clients**. This tool does **not** forge tokens to call
that API directly.

Instead it does the thing GovDeals' `robots.txt` explicitly allows:

1. **Discovery** — reads the public sitemap GovDeals publishes for crawlers and
   filters it to your seller(s). No browser, no auth. (Verified working.)
2. **Details** — loads each watched listing in a real headless browser, the same
   way a visitor would, lets the page's own frontend authenticate itself, and
   reads what rendered (capturing the JSON the page fetches, falling back to
   scraping the visible text). `robots.txt` asks for a 5-second crawl delay; the
   default is 6, and it only loads the handful of pages for your seller.

If you'd rather have a fully sanctioned structured feed, the **GSA Auctions API**
is public (free key at https://api.data.gov) — but it only covers *federal*
surplus, so it won't include state/university GovDeals listings like seller
7735. It's wired in as an optional bonus source, off by default.

## Finding a seller's ID

It's the second number in any listing URL:
`govdeals.com/en/asset/<assetId>/<sellerId>`. The lab listings live under
`.../7735`. Put the IDs you care about in `config.yaml` under `sellers:`.

## Setup

```bash
pip install -r requirements.txt
python -m playwright install chromium      # one-time browser download
cp config.example.yaml config.yaml         # then edit it
```

Edit `config.yaml`:
- `sellers` — seller IDs to watch
- `keywords` — optional title filter so you only get pinged about relevant gear
- `alert` — how close to closing + how cheap before it emails you
- `smtp` — for Gmail, generate an **App Password** (not your normal password)
- `valuation.backend` — `llm` uses the Anthropic API; set `none` to skip

Secrets via env vars (recommended over putting them in the file):
```bash
export ANTHROPIC_API_KEY=sk-ant-...        # for LLM valuation
export AUCTION_SMTP_PASSWORD=your-app-pw   # overrides smtp.password in config
```

## Running

```bash
python auction_watch.py discover   # record new listings for your sellers
python auction_watch.py refresh    # load them in a browser, update price/close
python auction_watch.py value      # fill in resale estimates
python auction_watch.py alert      # email anything closing soon + cheap
python auction_watch.py run        # all of the above, in order — use this in cron
python auction_watch.py list       # dump the DB to stdout
```

Twice-daily cron (catches anything entering its final 24h):
```cron
0 7,19 * * *  cd /path/to/auction-watch && /usr/bin/python3 auction_watch.py run >> watch.log 2>&1
```

## First run from your machine — important

I built and tested this from a cloud box, where GovDeals' CDN (Akamai) returns
**Access Denied** to the full browser load — it fingerprints datacenter IPs. From
your home connection with a normal browser fingerprint you won't hit that, and
the `refresh` step will tell you clearly if you do.

Because the listing page is a SPA, the exact field names / selectors can shift.
Run this **once** to capture what actually renders:

```bash
python auction_watch.py dump
```

That writes `dumps/<assetId>.txt` (visible text) and `dumps/<assetId>.json`
(the API responses the page fetched). If `current_price` / `close_at` come
through empty, open those files and adjust the key lists in
`govdeals.py` (`_from_json`) or the regexes in `parse_from_text`. Two-minute fix.

## On the valuation number

It's a **triage flag, not an appraisal.** For niche lab instruments, value from a
title alone is unreliable — condition, included software/licenses, and
calibration swing the real number many-fold. Every estimate carries a confidence
level, and the alert email says to verify before bidding. The honest check is
eBay **sold** listings plus someone who knows the instrument. There's an
`ebay_sold()` stub in `valuation.py` if you want to wire up a grounded signal via
eBay's official API later.

## Files

| file | what |
|---|---|
| `auction_watch.py` | CLI / orchestration |
| `govdeals.py` | sitemap discovery + browser detail capture |
| `gsa.py` | optional federal-surplus API source |
| `valuation.py` | pluggable resale estimate |
| `store.py` | SQLite persistence |
| `alerts.py` | SMTP email |
| `config.example.yaml` | copy to `config.yaml` |

## Limitations / gotchas

- Discovery is verified; **detail extraction needs that one first-run check** above.
- Polite by default (6s delay, one seller). Don't crank it down or widen to the
  whole site — that's how you earn a hard block and abuse someone's bandwidth.
- The LLM valuation costs a few cents per new listing and can be wrong.
- No bidding. This watches and notifies; you still place bids yourself.

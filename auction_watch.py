#!/usr/bin/env python3
"""
auction-watch — keep an eye on GovDeals listings for a seller, estimate resale
value, and email me when something closing soon is cheap.

Subcommands:
  discover   pull the sitemap, record NEW listings for your sellers
  refresh    load watched listings in a browser, update price/close/reserve
  value      fill in resale estimates for anything missing one
  alert      email listings closing within the window that are below threshold
  run        discover -> refresh -> value -> alert  (use this in cron)

  dump       like refresh but saves rendered HTML + captured API JSON to ./dumps
             so you can fix selectors/field-mapping on first run

Typical cron (twice a day):
  0 7,19 * * *  cd /path/to/auction-watch && /usr/bin/python3 auction_watch.py run >> watch.log 2>&1
"""
import sys
import argparse
from datetime import datetime, timezone, timedelta

import yaml

import govdeals
import valuation
import alerts
from store import Store


def load_cfg(path="config.yaml"):
    with open(path) as f:
        return yaml.safe_load(f)


def _matches_keywords(title, keywords):
    if not keywords:
        return True
    if not title:
        return True  # don't drop unknown-title items at discovery time
    t = title.lower()
    return any(k.lower() in t for k in keywords)


def cmd_discover(cfg, store):
    sellers = cfg["sellers"]
    found = added = 0
    for asset_id, seller_id, url in govdeals.discover_assets(sellers):
        found += 1
        if not store.seen(asset_id):
            store.add_new(asset_id, seller_id, url)
            added += 1
    print(f"discover: {found} listings for sellers {sellers}, {added} new")


def cmd_refresh(cfg, store, dump=False):
    rows = store.active()
    urls = [r["url"] for r in rows if r["url"]]
    if not urls:
        print("refresh: nothing active to refresh")
        return
    print(f"refresh: loading {len(urls)} listing(s) "
          f"({cfg['crawl']['delay_seconds']}s apart)...")
    try:
        results = govdeals.fetch_details(urls, cfg, dump=dump)
    except govdeals.AccessDenied:
        print("refresh: GovDeals returned Access Denied (CDN bot-block).\n"
              "  This host's IP is flagged. Run from a residential connection /\n"
              "  your normal machine. The sitemap discovery step still works.")
        return

    kw = cfg.get("keywords", [])
    updated = dropped = 0
    for r in rows:
        d = results.get(r["url"])
        if not d or "error" in d:
            continue
        # apply keyword filter once we actually know the title
        if not _matches_keywords(d.get("title"), kw):
            store.update_details(r["asset_id"], status="closed")  # stop tracking
            dropped += 1
            continue
        store.update_details(
            r["asset_id"],
            title=d.get("title") or r["title"],
            current_price=d.get("current_price"),
            currency=d.get("currency", "USD"),
            close_at=d.get("close_at"),
            reserve_met=d.get("reserve_met"),
            status="active",
        )
        updated += 1
    print(f"refresh: {updated} updated, {dropped} dropped (keyword filter)")


def cmd_value(cfg, store):
    rows = store.needs_estimate()
    print(f"value: estimating {len(rows)} listing(s)...")
    for r in rows:
        est = valuation.estimate(r["title"], r["description"] if "description" in r.keys() else "", cfg)
        store.set_estimate(r["asset_id"], r["source"], est["low"], est["high"], est["confidence"])
        print(f"  {r['asset_id']} {r['title'][:50]!r}: "
              f"{est['low']}-{est['high']} ({est['confidence']})")


def cmd_alert(cfg, store):
    a = cfg["alert"]
    window = timedelta(hours=a["hours_before_close"])
    now = datetime.now(timezone.utc)
    sent = 0
    for r in store.active():
        if not r["close_at"]:
            continue
        try:
            close = datetime.fromisoformat(r["close_at"])
        except ValueError:
            continue
        if close < now:
            store.update_details(r["asset_id"], status="closed")
            continue
        if close - now > window:
            continue                      # not closing soon enough yet
        if store.already_alerted(r["asset_id"], r["source"], "closing"):
            continue

        price = r["current_price"]
        trigger = False
        if price is not None:
            if r["est_low"] is not None and price <= a["value_ratio"] * r["est_low"]:
                trigger = True
            if a.get("hard_price_cap") is not None and price <= a["hard_price_cap"]:
                trigger = True
        if not trigger and a.get("also_alert_if_reserve_not_met") and r["reserve_met"] == 0:
            trigger = True
        if not trigger:
            continue

        subject, body = alerts.format_alert(r)
        try:
            alerts.send_email(cfg["smtp"], subject, body)
            store.record_alert(r["asset_id"], r["source"], "closing")
            sent += 1
            print(f"alert: emailed {r['asset_id']} ({r['title']})")
        except Exception as e:
            print(f"alert: FAILED to email {r['asset_id']}: {e}")
    print(f"alert: {sent} email(s) sent")


def cmd_gsa(cfg, store):
    g = cfg.get("gsa", {})
    if not g.get("enabled"):
        return
    import gsa as gsa_mod
    try:
        items = gsa_mod.fetch_gsa(g["api_key"])
    except Exception as e:
        print(f"gsa: fetch failed: {e}")
        return
    added = 0
    for it in items:
        if not store.seen(it["asset_id"], source="gsa"):
            store.add_new(it["asset_id"], None, it["url"], source="gsa", title=it["title"])
            store.update_details(it["asset_id"], source="gsa",
                                 current_price=it["current_price"],
                                 close_at=it["close_at"], status="active")
            added += 1
    print(f"gsa: {len(items)} federal listings, {added} new")


def main():
    ap = argparse.ArgumentParser(description="GovDeals auction watcher")
    ap.add_argument("command",
                    choices=["discover", "refresh", "value", "alert", "run", "dump", "list"])
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--db", default="auctions.db")
    args = ap.parse_args()

    cfg = load_cfg(args.config)
    store = Store(args.db)

    if args.command == "discover":
        cmd_discover(cfg, store)
    elif args.command == "refresh":
        cmd_refresh(cfg, store)
    elif args.command == "dump":
        cmd_refresh(cfg, store, dump=True)
    elif args.command == "value":
        cmd_value(cfg, store)
    elif args.command == "alert":
        cmd_alert(cfg, store)
    elif args.command == "list":
        for r in store.all_rows():
            print(dict(r))
    elif args.command == "run":
        cmd_discover(cfg, store)
        cmd_gsa(cfg, store)
        cmd_refresh(cfg, store)
        cmd_value(cfg, store)
        cmd_alert(cfg, store)


if __name__ == "__main__":
    sys.exit(main())

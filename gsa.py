"""
Optional federal-surplus source: the GSA Auctions public API. This one is
fully sanctioned - get a free key at https://api.data.gov (the public DEMO_KEY
is expired). It only covers FEDERAL surplus, so it will NOT contain the
state/university GovDeals listings (e.g. seller 7735); it's a clean bonus
channel, not a replacement.

Note: I couldn't confirm the live field names (dead demo key), so this maps
common-sounding keys defensively. Run `value`/`refresh` once and check; tweak
KEY_MAP below if a field comes through empty.
"""
import requests
from govdeals import _walk, _to_utc   # reuse the same defensive helpers

ENDPOINT = "https://api.gsa.gov/assets/gsaauctions/v2/auctions"


def fetch_gsa(api_key, timeout=60):
    """Return a list of normalized dicts: asset_id, url, title, current_price, close_at."""
    r = requests.get(ENDPOINT, params={"api_key": api_key, "format": "JSON"}, timeout=timeout)
    r.raise_for_status()
    payload = r.json()
    items = payload if isinstance(payload, list) else _walk(payload, {"auctions", "results", "data"}) or []
    out = []
    for it in items:
        aid = _walk(it, {"saleid", "auctionid", "id", "lotid", "itemid"})
        if aid is None:
            continue
        out.append({
            "asset_id": str(aid),
            "url": _walk(it, {"url", "link", "auctionurl"}) or "",
            "title": _walk(it, {"title", "name", "itemname", "description"}),
            "current_price": _walk(it, {"currentbid", "currentprice", "price", "minimumbid"}),
            "close_at": _to_utc(_walk(it, {"closedate", "enddate", "auctionclosedate",
                                           "biddingenddate"})),
        })
    return out

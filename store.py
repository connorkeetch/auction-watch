"""Tiny SQLite store. One row per asset, plus an alert-dedupe table."""
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    asset_id      TEXT,
    seller_id     TEXT,
    source        TEXT,          -- 'govdeals' or 'gsa'
    url           TEXT,
    title         TEXT,
    current_price REAL,
    currency      TEXT,
    close_at      TEXT,          -- ISO8601 UTC
    reserve_met   INTEGER,       -- 1 / 0 / NULL
    est_low       REAL,
    est_high      REAL,
    est_conf      TEXT,
    status        TEXT,          -- 'new' | 'active' | 'closed'
    first_seen    TEXT,
    last_checked  TEXT,
    PRIMARY KEY (asset_id, source)
);
CREATE TABLE IF NOT EXISTS alerts_sent (
    asset_id TEXT, source TEXT, stage TEXT, sent_at TEXT,
    PRIMARY KEY (asset_id, source, stage)
);
"""


def _now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path="auctions.db"):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def seen(self, asset_id, source="govdeals"):
        cur = self.db.execute(
            "SELECT 1 FROM assets WHERE asset_id=? AND source=?", (asset_id, source))
        return cur.fetchone() is not None

    def add_new(self, asset_id, seller_id, url, source="govdeals", title=None):
        self.db.execute(
            """INSERT OR IGNORE INTO assets
               (asset_id, seller_id, source, url, title, status, first_seen, last_checked)
               VALUES (?,?,?,?,?,'new',?,?)""",
            (asset_id, seller_id, source, url, title, _now(), None))
        self.db.commit()

    def update_details(self, asset_id, source="govdeals", **fields):
        fields["last_checked"] = _now()
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = list(fields.values()) + [asset_id, source]
        self.db.execute(f"UPDATE assets SET {cols} WHERE asset_id=? AND source=?", vals)
        self.db.commit()

    def set_estimate(self, asset_id, source, low, high, conf):
        self.update_details(asset_id, source, est_low=low, est_high=high, est_conf=conf)

    def needs_estimate(self):
        return self.db.execute(
            "SELECT * FROM assets WHERE est_conf IS NULL AND title IS NOT NULL").fetchall()

    def active(self):
        """Anything not closed — candidates for a detail refresh."""
        return self.db.execute(
            "SELECT * FROM assets WHERE status != 'closed' OR status IS NULL").fetchall()

    def all_rows(self):
        return self.db.execute("SELECT * FROM assets").fetchall()

    def already_alerted(self, asset_id, source, stage):
        return self.db.execute(
            "SELECT 1 FROM alerts_sent WHERE asset_id=? AND source=? AND stage=?",
            (asset_id, source, stage)).fetchone() is not None

    def record_alert(self, asset_id, source, stage):
        self.db.execute(
            "INSERT OR IGNORE INTO alerts_sent VALUES (?,?,?,?)",
            (asset_id, source, stage, _now()))
        self.db.commit()

"""Email alerts over SMTP (SSL). Gmail: use an App Password."""
import os
import ssl
import smtplib
from email.message import EmailMessage


def send_email(smtp_cfg, subject, body):
    password = os.getenv("AUCTION_SMTP_PASSWORD") or smtp_cfg.get("password")
    msg = EmailMessage()
    msg["From"] = smtp_cfg["user"]
    msg["To"] = smtp_cfg["to"]
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP_SSL(smtp_cfg["host"], smtp_cfg["port"],
                          context=ssl.create_default_context()) as s:
        s.login(smtp_cfg["user"], password)
        s.send_message(msg)


def format_alert(row):
    """row: sqlite3.Row for an asset. Returns (subject, body)."""
    title = row["title"] or f"Asset {row['asset_id']}"
    price = row["current_price"]
    price_s = f"${price:,.2f}" if price is not None else "n/a"
    if row["est_low"] is not None:
        est_s = f"${row['est_low']:,.0f}-${row['est_high']:,.0f} ({row['est_conf']} confidence)"
    else:
        est_s = "no estimate"
    reserve = {1: "met", 0: "NOT met"}.get(row["reserve_met"], "unknown")

    subject = f"[auction] {title[:60]} — {price_s}, closes soon"
    body = (
        f"{title}\n"
        f"{row['url']}\n\n"
        f"Current price : {price_s}\n"
        f"Est. resale   : {est_s}\n"
        f"Reserve       : {reserve}\n"
        f"Closes (UTC)  : {row['close_at']}\n\n"
        "Heads up: the resale estimate is a rough triage number, not an "
        "appraisal. For niche gear it can be off by a lot. Check eBay sold "
        "listings and the actual condition/included software before you bid.\n"
    )
    return subject, body

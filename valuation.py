"""
Rough resale valuation. This is a TRIAGE SIGNAL, not an appraisal.

For niche lab gear an LLM (or anyone) guessing from a title is unreliable -
condition, included software/licenses, and calibration status swing the real
number by 5-10x. So every estimate carries a confidence flag, and the alert
email tells you to verify before bidding. The honest check is eBay sold
listings plus someone who knows the instrument.
"""
import os
import re
import json

UNKNOWN = {"low": None, "high": None, "confidence": "none",
           "basis": "no valuation backend"}

PROMPT = """You estimate the USED RESALE value of surplus/lab/industrial equipment.
Given an auction listing title, return ONLY a JSON object, no prose, no markdown:
{{"low": <int usd>, "high": <int usd>, "confidence": "low"|"medium"|"high",
  "basis": "<one short sentence>"}}
Rules:
- Resale-to-a-private-buyer value, NOT new/MSRP.
- If the title is too vague to value, use confidence "low" and a wide range.
- Niche scientific instruments: default confidence "low" unless the model is
  well known and commonly resold.
Title: {title}
Extra: {extra}"""


def _llm(title, extra, model):
    try:
        import anthropic
    except ImportError:
        return dict(UNKNOWN, basis="anthropic package not installed")
    if not os.getenv("ANTHROPIC_API_KEY"):
        return dict(UNKNOWN, basis="ANTHROPIC_API_KEY not set")
    try:
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=model, max_tokens=300,
            messages=[{"role": "user",
                       "content": PROMPT.format(title=title, extra=extra or "")}])
        text = "".join(b.text for b in msg.content if b.type == "text")
        text = re.sub(r"```(?:json)?|```", "", text).strip()
        d = json.loads(text)
        return {"low": d.get("low"), "high": d.get("high"),
                "confidence": d.get("confidence", "low"),
                "basis": d.get("basis", "")}
    except Exception as e:
        return dict(UNKNOWN, basis=f"valuation error: {e}")


def estimate(title, description="", cfg=None):
    cfg = cfg or {}
    vcfg = cfg.get("valuation", {})
    if vcfg.get("backend") == "llm":
        return _llm(title, description, vcfg.get("model", "claude-sonnet-4-6"))
    return dict(UNKNOWN)


# --- Optional, experimental: a more grounded signal from eBay sold listings.
# eBay also fights scrapers; treat as best-effort and rate-limit. Left as a
# stub on purpose so you opt in deliberately rather than hammering them.
def ebay_sold(title):
    raise NotImplementedError(
        "Implement against eBay's Marketplace Insights API (sold-item data) "
        "or the Finding API. Manual scraping of sold listings violates eBay ToS.")

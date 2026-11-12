"""Extract stated project values and order opportunities by match, then value."""

from decimal import Decimal, InvalidOperation
import re

VALUE_PATTERN = re.compile(
    r"\b(?:estimated\s+(?:tender\s+)?(?:value|cost)|tender\s+(?:value|amount)|"
    r"project\s+(?:value|cost)|contract\s+value)\s*(?:is|of|:|=|-)?\s*"
    r"(?P<currency>₹|INR\b|Rs\b\.?|rupees\b)?\s*"
    r"(?P<amount>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<unit>crores?|cr\.?|lakhs?|lacs?|million|billion|thousand|k)?\b",
    re.IGNORECASE,
)
MULTIPLIERS = {"crore": 10000000, "cr": 10000000, "lakh": 100000,
               "lac": 100000, "million": 1000000, "billion": 1000000000,
               "thousand": 1000, "k": 1000}


def format_inr(amount):
    if amount is None:
        return "Not stated"
    if amount >= 10000000:
        return f"₹{amount / 10000000:,.2f} crore"
    if amount >= 100000:
        return f"₹{amount / 100000:,.2f} lakh"
    return f"₹{amount:,.0f}"


def with_tender_value(item):
    found = {}
    for text in [item.get("title", ""), *item.get("snippets", [])]:
        for match in VALUE_PATTERN.finditer(text):
            unit = (match.group("unit") or "").lower().rstrip(".").rstrip("s")
            # Bare numbers aren't money. Indian denominations imply INR only;
            # international scales need an explicit rupee currency marker.
            if not match.group("currency") and unit not in ("crore", "cr", "lakh", "lac"):
                continue
            try:
                value = Decimal(match.group("amount").replace(",", "")) * MULTIPLIERS.get(unit, 1)
            except InvalidOperation:
                continue
            if value > 0:
                found.setdefault(float(value), match.group(0).strip())
    conflict = len(found) > 1
    amount = next(iter(found)) if len(found) == 1 else None
    if not found and item.get('value_fallback'):
        historical = with_tender_value({'title': '', 'snippets': [item['value_fallback']['text']]})
        if historical['tender_value_inr'] is not None:
            return {**item, **{key: historical[key] for key in (
                'tender_value_inr', 'tender_value_display', 'tender_value_evidence')},
                    'tender_value_status': 'previously stated; verify latest source'}
    return {**item, "tender_value_inr": amount,
            "tender_value_display": "Conflicting values — verify" if conflict else format_inr(amount),
            "tender_value_status": "conflicting" if conflict else "stated in search text; unverified" if amount is not None else "not stated",
            "tender_value_evidence": list(found.values())}


def opportunity_sort_key(item):
    amount = item.get("tender_value_inr")
    return (-item["score"], -(amount if amount is not None else -1), item["title"].casefold())


def prepare_report(report):
    if report is None:
        return None
    return {**report,
            "candidates": sorted((with_tender_value(item) for item in report["candidates"]),
                                 key=opportunity_sort_key),
            "ranking_order": ["match score descending", "stated INR value descending", "title"]}

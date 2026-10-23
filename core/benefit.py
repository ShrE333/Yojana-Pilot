"""What a scheme is actually worth to one person, in comparable rupees per year.

Scheme pages state value in incompatible ways, and comparing the raw numbers ranks badly:

    "Rs 20,00,000 for integrated development of SC majority villages"  -> a village budget
    "Cashless cover of up to Rs 5,00,000 per family per annum"         -> an insurance limit
    "Rs 1,01,000 in installments from birth until the girl is 18"      -> spread over 18 years
    "Rs 60 per month for 10 months"                                    -> Rs 600 a year
    "Free accommodation, food, bedding, library"                       -> no number at all

So each benefit is reduced to two things code can rank on:
    personal        does a person or family receive it, or an institution / village / panchayat
    annual_value    rupees per year this person could actually expect

Nothing here calls an LLM. Amounts come from the extracted fields when present, and are otherwise
read out of the benefit text, so the 250+ schemes already stored are upgraded without re-reading them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# How much of a headline number a person really gets in a year.
ONE_TIME_SPREAD = 3.0      # a one-off grant, counted over the years it helps
CHILD_SCHEME_YEARS = 18.0  # "from birth until the girl turns 18"
INSURANCE_RATE = 0.04      # a cover limit is not income: roughly its yearly worth

# When a page gives no number, fall back to the extractor's 1-5 usefulness guess.
VALUE_BAND = {1: 1_000, 2: 3_000, 3: 8_000, 4: 20_000, 5: 40_000}

# Recipients that are not a person or a household.
NOT_PERSONAL = re.compile(
    r"\b(village|villages|gram panchayat|panchayati raj|panchayat|institution|institutions|"
    r"organi[sz]ation|organi[sz]ations|school|schools|college|colleges|hostel buildings|"
    r"ngo|ngos|society|societies|committee|department|municipal|corporation|unit of \d+)\b"
    r"|ग्रामपंचायत|संस्था|संस्थे|विद्यालय", re.I)
PERSONAL_HINT = re.compile(
    r"\b(beneficiar|applicant|student|students|girl|woman|women|farmer|worker|patient|"
    r"citizen|family|families|per person|per family|household)\b"
    r"|लाभार्थी|विद्यार्थ|मुली|शेतकरी|कुटुंब", re.I)

INSURANCE = re.compile(r"\b(insur|cover|cashless|claim|mediclaim|hospital|treatment)", re.I)
# "up to Rs 5 lakh" is a ceiling, not an amount anyone receives
CEILING = re.compile(r"\b(up ?to|upto|maximum of|not exceeding|limit of)\b", re.I)
MONTHLY = re.compile(r"\b(per month|a month|monthly|/ ?month|प्रति महिना|दरमहा)\b", re.I)
YEARLY = re.compile(r"\b(per year|a year|per annum|yearly|annually|/ ?year|प्रति वर्ष|वार्षिक)\b", re.I)
ONE_TIME = re.compile(r"\b(one[- ]time|one off|lump ?sum|single instal|एकरकमी|एकदाच)\b", re.I)
# wording that means a single grant even when the page never says "one-time"
ONE_TIME_HINT = re.compile(r"\b(construction of (?:a )?house|house construction|dwelling unit|"
                           r"marriage assistance|funeral|death of|capital subsidy|purchase of|"
                           r"award|prize)\b|घरकुल|विवाह", re.I)
UNTIL_18 = re.compile(r"\b(until|till|upto|up to)\b[^.]{0,40}\b(18|eighteen)\b[^.]{0,20}\b(year|age)", re.I)
MONTHS_FOR = re.compile(r"for\s+(\d{1,2})\s+months", re.I)
FREE_SERVICE = re.compile(r"\b(free|no cost|without charge|नि:?शुल्क|मोफत)\b", re.I)

# "Rs. 1,01,000" / "₹15,000" / "Rs 30.00 lakhs" / "2 crore"
AMOUNT = re.compile(
    r"(?:₹|rs\.?|rupees|inr)\s*([\d,]+(?:\.\d+)?)\s*(lakhs?|lakh|crores?|crore|thousand)?"
    r"|([\d,]+(?:\.\d+)?)\s*(lakhs?|lakh|crores?|crore)\b", re.I)
MULTIPLIER = {"lakh": 100_000, "lakhs": 100_000, "crore": 10_000_000, "crores": 10_000_000,
              "thousand": 1_000}


@dataclass
class Benefit:
    annual_value: int          # comparable rupees per year for this person
    personal: bool             # False when a village, school or institution is the recipient
    kind: str                  # cash | insurance | in_kind | unknown
    basis: str                 # short, human-readable reason, shown in the UI


def amounts_in(text: str) -> list[int]:
    out = []
    for m in AMOUNT.finditer(text or ""):
        raw, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        out.append(int(value * MULTIPLIER.get((unit or "").lower(), 1)))
    return out


def is_personal(scheme: dict) -> bool:
    """Does a person or family get this, rather than a village, school or institution?"""
    recipient = (scheme.get("benefit_recipient") or "").lower()
    if recipient in ("individual", "family", "person", "household"):
        return True
    if recipient in ("village", "institution", "group", "organisation", "organization", "panchayat"):
        return False
    text = f"{scheme.get('name', '')} {scheme.get('benefit_summary', '')}"
    if NOT_PERSONAL.search(text) and not PERSONAL_HINT.search(text):
        return False
    return True


def annual_value(scheme: dict) -> tuple[int, str, str]:
    """(rupees per year, kind, why). Reads the extracted fields first, then the benefit text."""
    text = f"{scheme.get('benefit_summary') or ''} {scheme.get('name') or ''}"
    # benefit_annual_inr is already a yearly figure; never multiply it by a period again
    annual_field = int(scheme.get("benefit_annual_inr") or 0)
    raw_field = int(scheme.get("benefit_amount_inr") or 0)
    found = amounts_in(text)
    amount = raw_field or annual_field or (max(found) if found else 0)

    if amount and (INSURANCE.search(text) or CEILING.search(text)):
        yearly = int(amount * INSURANCE_RATE)
        what = "cover" if INSURANCE.search(text) else "ceiling"
        return yearly, "insurance", f"{what} of up to Rs {amount:,}, worth about Rs {yearly:,} a year"
    if annual_field and not raw_field:
        # The field is meant to be yearly, but extractors often put a one-off grant in it (PMAY's
        # Rs 1,30,000 house grant is not annual income). So it may only be reduced, never multiplied
        # by a period: multiplying is what turned a Rs 200/month pension into Rs 28,800 a year.
        if ONE_TIME.search(text) or ONE_TIME_HINT.search(text):
            return int(annual_field / ONE_TIME_SPREAD), "cash", f"one-time Rs {annual_field:,}"
        if annual_field >= 50_000 and not (YEARLY.search(text) or MONTHLY.search(text)):
            return int(annual_field / ONE_TIME_SPREAD), "cash", f"Rs {annual_field:,}, assumed one-time"
        return annual_field, "cash", f"Rs {annual_field:,} a year"
    if not amount:
        if FREE_SERVICE.search(text):
            band = VALUE_BAND.get(scheme.get("rank", {}).get("value_score", 3), 8_000)
            return band, "in_kind", "free service or goods, no amount given"
        band = VALUE_BAND.get(scheme.get("rank", {}).get("value_score", 3), 8_000)
        return band, "unknown", "no amount stated on the page"

    period = (scheme.get("benefit_period") or "").lower()
    if period == "monthly" or (not period and MONTHLY.search(text)):
        months = int(m.group(1)) if (m := MONTHS_FOR.search(text)) else 12
        return amount * months, "cash", f"Rs {amount:,} a month for {months} months"
    if UNTIL_18.search(text):
        return int(amount / CHILD_SCHEME_YEARS), "cash", f"Rs {amount:,} spread to age 18"
    if period in ("one_time", "once") or ONE_TIME.search(text):
        return int(amount / ONE_TIME_SPREAD), "cash", f"one-time Rs {amount:,}"
    if period == "yearly" or YEARLY.search(text):
        return amount, "cash", f"Rs {amount:,} a year"
    # no period stated: treat a large sum as one-off, a small one as yearly
    if amount >= 50_000:
        return int(amount / ONE_TIME_SPREAD), "cash", f"Rs {amount:,}, assumed one-time"
    return amount, "cash", f"Rs {amount:,} a year"


def benefit_of(scheme: dict) -> Benefit:
    value, kind, basis = annual_value(scheme)
    personal = is_personal(scheme)
    if not personal:
        basis = "paid to a village or institution, not to a person"
    return Benefit(annual_value=value if personal else 0, personal=personal, kind=kind, basis=basis)

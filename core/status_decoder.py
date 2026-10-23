"""Map a portal status the user reports to ranked likely causes and next actions.

decode_status("pm_kisan", "eKYC is pending", flags={"name_mismatch"})
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz, process

from .loader import load_schemes

BOOST = 0.35   # added to a cause's weight when pre-flight found that exact problem

# common ways people type/say the same status
ALIASES = {
    "kyc": "ekyc", "ईकेवायसी": "ekyc", "केवायसी": "ekyc",
    "fail": "failed", "failure": "failed", "अयशस्वी": "failed", "असफल": "failed",
    "reject": "rejected", "नामंजूर": "rejected", "अस्वीकृत": "rejected",
    "pending": "pending", "प्रलंबित": "pending", "लंबित": "pending",
    "पेमेंट": "payment", "भुगतान": "payment", "पैसे": "payment",
}


@dataclass
class Cause:
    action: str
    text: str
    where: str
    score: float
    boosted: bool


@dataclass
class DecodedStatus:
    scheme_id: str
    reported: str
    matched_status: str | None
    match_score: float
    causes: list[Cause]
    note: str = "These are likely causes, not certainties. Official grievance channels remain available."


def _clean(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"\be[\s-]kyc\b", "ekyc", t)
    t = re.sub(r"[^\w\sऀ-ॿ]", " ", t)   # keep Devanagari vowel signs
    # whole-word replacement only, so "failed" does not become "faileded"
    t = " ".join(ALIASES.get(w, w) for w in t.split())
    t = re.sub(r"\b(is|status|shows|showing|the|my|it|says)\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def decode_status(scheme_id: str, status_text: str, flags: set[str] | None = None,
                  schemes: dict | None = None, min_score: float = 70.0, top_k: int = 3) -> DecodedStatus:
    schemes = schemes or load_schemes()
    scheme = schemes[scheme_id]
    flags = flags or set()
    status_map = scheme["status_map"]

    cleaned = _clean(status_text)
    best = process.extractOne(cleaned, list(status_map.keys()), scorer=fuzz.token_set_ratio)
    if not best or best[1] < min_score:
        return DecodedStatus(scheme_id, status_text, None, best[1] if best else 0.0, [],
                             note="Status not recognised. Ask the user to read it exactly as shown on the portal.")

    key, match_score, _ = best
    causes = []
    for c in status_map[key]:
        boosted = bool(c.get("boost_if") and c["boost_if"] in flags)
        act = scheme["actions"][c["action"]]
        causes.append(Cause(c["action"], act["text"], act["where"],
                            round(c["weight"] + (BOOST if boosted else 0.0), 3), boosted))
    causes.sort(key=lambda c: -c.score)
    return DecodedStatus(scheme_id, status_text, key, float(match_score), causes[:top_k])

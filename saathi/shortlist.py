"""Deterministic step [D]: run the engine over the whole catalog and package the result as JSON.

Nothing here calls an LLM. The agent only rewords what build_shortlist returns.
"""
from __future__ import annotations

from rapidfuzz import fuzz

from core.awareness import awareness
from core.benefit import benefit_of
from core.eligibility import SchemeResult, evaluate_scheme
from core.ranking import lesser_known, rank_schemes
from discovery.catalog import norm_name

SHOWN_STATUSES = ("eligible", "eligible_check_manually", "near_miss")


def card(r: SchemeResult, scheme: dict, scored=None) -> dict:
    return {
        "rank_score": scored.score if scored else None,
        "match": scored.match if scored else None,
        "annual_value_inr": scored.value if scored else benefit_of(scheme).annual_value,
        "why_ranked": scored.reasons if scored else [],
        "id": r.scheme_id,
        "name": r.name,
        "status": r.status,
        "why": r.explain(),
        "benefit": scheme.get("benefit_summary") or "",
        "benefit_annual_inr": scheme.get("benefit_annual_inr") or 0,
        "documents": [d["name"] for d in scheme.get("documents") or []][:8],
        "how_to_apply": scheme.get("how_to_apply") or "",
        "apply_url": scheme.get("apply_url") or "",
        "source_url": r.source_url,
        "source_site": scheme.get("source_site") or "",
        "verified": r.verified,
        "awareness": awareness(scheme),
        "agency": scheme.get("agency_name") or scheme.get("ministry") or "",
        "district": scheme.get("district"),
        "rules_met": [t.reason or f"{t.field} {t.op} {t.expected}" for t in r.trace if t.outcome == "pass"],
        "check_yourself": r.unmapped_criteria,
    }


def build_shortlist(profile: dict, catalog: dict, n_others: int = 4) -> dict:
    results = [evaluate_scheme(s, profile) for s in catalog.values()]
    ranking = rank_schemes(results, catalog)
    others = ranking.queue[:n_others]

    def made(r):
        return card(r, catalog[r.scheme_id], ranking.scored.get(r.scheme_id))

    return {
        "start_with": made(ranking.start_with) if ranking.start_with else None,
        "others": [made(r) for r in others],
        "more_eligible_not_shown": max(0, len(ranking.queue) - n_others),
        "lesser_known": [made(r) for r in lesser_known(results, catalog)],
        "near_misses": [card(r, catalog[r.scheme_id]) for r in ranking.near_misses[:3]],
        "check_on_official_page": [{"name": r.name, "source_url": r.source_url,
                                    "conditions": r.unmapped_criteria[:3]} for r in ranking.check_manually[:3]],
        "need_more_info": len(ranking.pending),
        "schemes_checked": len(results),
    }


def find_scheme(name: str, catalog: dict) -> str | None:
    key = norm_name(name or "")
    if not key:
        return None
    best, best_score = None, 0.0
    for sid, s in catalog.items():
        score = max(fuzz.token_set_ratio(key, norm_name(s["name"])),
                    fuzz.token_set_ratio(key, norm_name(sid.replace("_", " "))))
        if score > best_score:
            best, best_score = sid, score
    return best if best_score >= 85 else None


def scheme_details(sid: str, profile: dict, catalog: dict) -> dict:
    scheme = catalog[sid]
    r = evaluate_scheme(scheme, profile)
    out = card(r, scheme)
    out["rules"] = [{"rule": t.reason or f"{t.field} {t.op} {t.expected}", "result_for_this_person": t.outcome}
                    for t in r.trace]
    out["missing_information"] = r.missing_fields
    return out

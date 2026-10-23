"""One call that runs the whole core: the function the agent's tools will wrap later.

assist(profile, records=None, status=None, scheme_id=None) -> dict (JSON-serialisable)
"""
from __future__ import annotations

from dataclasses import asdict

from .checklist import build_checklist
from .eligibility import check_eligibility
from .loader import load_schemes
from .preflight import preflight_check
from .questions import next_best_question
from .ranking import rank_schemes
from .status_decoder import decode_status


def assist(profile: dict, records: dict | None = None, status: str | None = None,
           scheme_id: str | None = None, have_documents: set[str] | None = None,
           schemes: dict | None = None) -> dict:
    schemes = schemes or load_schemes()
    results = check_eligibility(profile, schemes)
    ranking = rank_schemes(results, schemes)

    out: dict = {
        "results": [{"scheme": r.scheme_id, "status": r.status, "why": r.explain(),
                     "source": r.source_url, "last_verified": r.last_verified} for r in results],
        "start_with": ranking.start_with.scheme_id if ranking.start_with else None,
        "queue": [r.scheme_id for r in ranking.queue],
        "near_misses": [r.explain() for r in ranking.near_misses],
        "next_question": None,
    }
    q = next_best_question(profile, schemes)
    if q:
        out["next_question"] = {"id": q["id"], "en": q["text_en"], "hi": q["text_hi"], "mr": q["text_mr"]}

    target = scheme_id or out["start_with"]
    if target:
        pf = preflight_check(target, records, profile, schemes)
        out["preflight"] = {
            "scheme": target, "ready": pf.ready,
            "fixes": [asdict(f) for f in pf.fixes],
            "checks": [asdict(c) for c in pf.checks],
            "unknown_dependencies": pf.unknown_dependencies,
        }
        out["checklist"] = build_checklist(target, have_documents, schemes)
        if status:
            ds = decode_status(target, status, pf.flags, schemes)
            out["status"] = {"matched": ds.matched_status, "causes": [asdict(c) for c in ds.causes],
                             "note": ds.note}
    return out

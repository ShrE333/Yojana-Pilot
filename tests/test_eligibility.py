from pathlib import Path

import pytest
import yaml

from core import check_eligibility, rank_schemes
from core.eligibility import evaluate_rule
from core.loader import SchemeError, _validate, load_schemes

PERSONAS = yaml.safe_load((Path(__file__).parent / "personas.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("persona", PERSONAS, ids=[p["id"] for p in PERSONAS])
def test_persona_statuses(persona):
    results = {r.scheme_id: r.status for r in check_eligibility(persona["profile"])}
    assert results == persona["expected"], persona["note"]


@pytest.mark.parametrize("persona", PERSONAS, ids=[p["id"] for p in PERSONAS])
def test_persona_start_with(persona):
    ranking = rank_schemes(check_eligibility(persona["profile"]))
    got = ranking.start_with.scheme_id if ranking.start_with else None
    assert got == persona["start_with"], persona["note"]


def test_every_scheme_has_source_and_date():
    for s in load_schemes().values():
        assert s["source_url"].startswith("http")
        assert str(s["last_verified"])[:4].isdigit()


def test_near_miss_reports_gap():
    res = {r.scheme_id: r for r in check_eligibility({"age": 58, "owns_cultivable_land": False,
                                                      "is_bpl": True, "in_pmjay_beneficiary_list": False})}
    r = res["ignoaps"]
    assert r.status == "near_miss"
    assert r.failed_rules[0].gap == 2
    assert "short by 2" in r.explain()


def test_hard_fail_beats_unknown():
    # one clear failure decides the scheme even if other fields are unknown
    res = {r.scheme_id: r for r in check_eligibility({"owns_cultivable_land": False})}
    assert res["pm_kisan"].status == "ineligible"


def test_bad_types_do_not_crash():
    r = evaluate_rule({"field": "age", "op": "gte", "value": 60}, {"age": "sixty"})
    assert r.outcome == "unknown"


def test_trace_contains_every_rule():
    res = {r.scheme_id: r for r in check_eligibility({"age": 30})}
    assert len(res["pm_kisan"].trace) == 9


def test_validator_rejects_unknown_action():
    bad = {"id": "x", "name": "x", "source_url": "http://x", "last_verified": "2026-01-01",
           "rank": {"value_score": 1, "effort": 1},
           "eligibility": {"all": [{"field": "age", "op": "gte", "value": 1}]},
           "actions": {}, "status_map": {"failed": [{"action": "missing", "weight": 1}]}}
    with pytest.raises(SchemeError):
        _validate(bad, Path("x.yaml"))


def test_validator_rejects_bad_op():
    bad = {"id": "x", "name": "x", "source_url": "http://x", "last_verified": "2026-01-01",
           "rank": {"value_score": 1, "effort": 1},
           "eligibility": {"all": [{"field": "age", "op": "bigger", "value": 1}]},
           "actions": {}, "status_map": {}}
    with pytest.raises(SchemeError):
        _validate(bad, Path("x.yaml"))

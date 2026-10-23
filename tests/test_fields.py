"""Field vocabulary (schemes/fields.yaml) and the loader checks that depend on it."""
from pathlib import Path

import jsonschema
import pytest

from core.fields import FieldError, check_value, load_fields, profile_json_schema, validate_profile
from core.loader import SchemeError, _validate, load_questions, load_schemes


def _scheme(rules, **extra):
    s = {"id": "x", "name": "x", "source_url": "http://x", "last_verified": "2026-01-01",
         "rank": {"value_score": 1, "effort": 1}, "eligibility": {"all": rules},
         "actions": {"fix": {"text": "t", "where": "w"}}, "status_map": {}}
    s.update(extra)
    return s


def _reject(scheme, match):
    with pytest.raises(SchemeError, match=match):
        _validate(scheme, Path("x.yaml"))


# ---------- the vocabulary itself ----------

def test_vocabulary_is_broad_and_well_formed():
    fields = load_fields()
    assert len(fields) >= 40
    for name in ("state", "gender", "social_category", "annual_family_income_inr", "occupation",
                 "disability_percent", "marital_status", "current_study_level"):
        assert name in fields
    assert len(fields["state"]["values"]) == 36          # 28 states + 8 union territories


def test_every_field_used_by_curated_schemes_and_questions_exists():
    fields = load_fields()
    for s in load_schemes().values():
        for rule in (s["eligibility"].get("all") or []) + (s["eligibility"].get("any") or []):
            assert rule["field"] in fields
    for q in load_questions():
        assert set(q["fields"]) <= set(fields)


def test_question_references_in_fields_exist():
    qids = {q["id"] for q in load_questions()}
    for name, spec in load_fields().items():
        if spec.get("question"):
            assert spec["question"] in qids, name


# ---------- check_value ----------

@pytest.mark.parametrize("name,value,expected", [
    ("age", 62, 62), ("age", 62.0, 62), ("is_bpl", True, True), ("state", "maharashtra", "maharashtra"),
    ("annual_family_income_inr", 150000.5, 150000.5), ("district", "  Pune ", "pune"),
    ("district", "Dharashiv", "osmanabad"), ("district", "Mumbai City", "mumbai_city"),
])
def test_check_value_accepts(name, value, expected):
    assert check_value(name, value) == expected


@pytest.mark.parametrize("name,value", [
    ("age", "sixty"), ("age", 62.5), ("age", True), ("age", 200), ("is_bpl", "yes"),
    ("state", "bombay"), ("disability_percent", -5), ("district", ""), ("no_such_field", 1),
])
def test_check_value_rejects(name, value):
    with pytest.raises(FieldError):
        check_value(name, value)


def test_validate_profile_drops_bad_and_unknown_keeps_internal():
    clean, errors = validate_profile({"age": 70, "is_bpl": "yes", "aadhaar": "1234",
                                      "_needs_followup": "exclusions", "gender": None})
    assert clean == {"age": 70, "_needs_followup": "exclusions", "gender": None}
    assert len(errors) == 2


def test_profile_json_schema_validates_extractor_output():
    schema = profile_json_schema(["age", "state", "is_bpl"])
    jsonschema.validate({"age": 62, "state": "maharashtra", "is_bpl": None}, schema)
    for bad in ({"age": -1}, {"state": "bombay"}, {"aadhaar": "1"}):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, schema)


# ---------- loader rejects rules that do not fit the vocabulary ----------

def test_loader_rejects_unknown_field():
    _reject(_scheme([{"field": "is_poor", "op": "eq", "value": True}]), "unknown field 'is_poor'")


def test_loader_rejects_wrong_value_type():
    _reject(_scheme([{"field": "age", "op": "gte", "value": "60"}]), "expected a number")
    _reject(_scheme([{"field": "is_bpl", "op": "eq", "value": "yes"}]), "true/false")


def test_loader_rejects_bad_enum_and_list_ops():
    _reject(_scheme([{"field": "state", "op": "eq", "value": "bombay"}]), "not one of")
    _reject(_scheme([{"field": "social_category", "op": "in", "value": "sc"}]), "non-empty list")
    _reject(_scheme([{"field": "social_category", "op": "in", "value": ["sc", "dalit"]}]), "not one of")


def test_loader_rejects_order_op_on_non_numeric_field():
    _reject(_scheme([{"field": "state", "op": "gte", "value": "goa"}]), "numeric field")


def test_loader_rejects_bad_near_miss():
    _reject(_scheme([{"field": "is_bpl", "op": "eq", "value": True, "near_miss": 1}]), "near_miss")


def test_loader_rejects_unknown_dependency():
    s = _scheme([{"field": "age", "op": "gte", "value": 60}],
                dependencies=[{"id": "magic_done", "label": "x", "fix": "fix"}])
    _reject(s, "magic_done")


def test_unmapped_criteria_accepted_and_checked():
    ok = _scheme([{"field": "age", "op": "gte", "value": 18}],
                 unmapped_criteria=["Must have lived in the state for 15 years"])
    _validate(ok, Path("x.yaml"))
    _reject(_scheme([{"field": "age", "op": "gte", "value": 18}], unmapped_criteria="text"), "unmapped_criteria")


def test_valid_rich_scheme_passes():
    _validate(_scheme([
        {"field": "state", "op": "eq", "value": "maharashtra"},
        {"field": "gender", "op": "eq", "value": "female"},
        {"field": "social_category", "op": "in", "value": ["sc", "st"]},
        {"field": "annual_family_income_inr", "op": "lte", "value": 250000, "near_miss": 20000},
    ]), Path("x.yaml"))

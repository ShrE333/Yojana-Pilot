import pytest

from core import assist, build_checklist, decode_status, preflight_check


@pytest.mark.parametrize("text,expected", [
    ("eKYC pending", "ekyc pending"),
    ("e-KYC is pending", "ekyc pending"),
    ("KYC pending", "ekyc pending"),
    ("Payment Failed", "payment failed"),
    ("payment fail", "payment failed"),
    ("Land Seeding : No", "land seeding no"),
    ("केवायसी प्रलंबित", "ekyc pending"),
    ("पेमेंट असफल", "payment failed"),
])
def test_status_matching(text, expected):
    assert decode_status("pm_kisan", text).matched_status == expected


def test_unknown_status_returns_no_causes():
    d = decode_status("pm_kisan", "something weird")
    assert d.matched_status is None and d.causes == []


def test_flags_boost_name_mismatch():
    plain = decode_status("pm_kisan", "Payment Failed")
    boosted = decode_status("pm_kisan", "Payment Failed", flags={"name_mismatch"})
    assert plain.causes[0].action == "npci_seeding"
    assert boosted.causes[0].action == "fix_name_mismatch" and boosted.causes[0].boosted


def test_causes_capped_at_three():
    assert len(decode_status("pm_kisan", "Payment Failed").causes) <= 3


def test_preflight_catches_sunil_suneel():
    pf = preflight_check("pm_kisan",
                         {"aadhaar": {"name": "Sunil Patil"}, "land_record": {"name": "Suneel Patil"}},
                         {"ekyc_done": True, "land_record_seeded": True,
                          "bank_aadhaar_npci_seeded": True, "bank_account_active": True})
    assert not pf.ready
    assert [f.action for f in pf.fixes] == ["fix_name_mismatch"]
    assert "name_mismatch" in pf.flags


def test_preflight_dependency_order():
    pf = preflight_check("pm_kisan", {}, {"ekyc_done": False, "land_record_seeded": True,
                                          "bank_aadhaar_npci_seeded": False, "bank_account_active": False})
    assert [f.action for f in pf.fixes] == ["reactivate_account", "npci_seeding", "complete_ekyc"]


def test_preflight_ready_when_all_good():
    pf = preflight_check("pm_kisan",
                         {"aadhaar": {"name": "Sunil Patil"}, "land_record": {"name": "SUNIL PATIL"},
                          "bank_passbook": {"name": "Patil Sunil"}},
                         {"ekyc_done": True, "land_record_seeded": True,
                          "bank_aadhaar_npci_seeded": True, "bank_account_active": True})
    assert pf.ready and pf.fixes == []


def test_preflight_lists_unknown_dependencies():
    pf = preflight_check("pm_kisan", {}, {})
    assert set(pf.unknown_dependencies) == {"ekyc_done", "land_record_seeded",
                                            "bank_aadhaar_npci_seeded", "bank_account_active"}
    assert not pf.ready


def test_checklist_marks_documents():
    cl = build_checklist("pm_kisan", have={"aadhaar"})
    status = {d["key"]: d["status"] for d in cl}
    assert status == {"aadhaar": "have", "land_record": "missing", "bank_passbook": "missing"}


def test_assist_end_to_end_demo_case():
    """The demo story: Sunil/Suneel, eKYC pending."""
    out = assist(
        profile={"age": 52, "owns_cultivable_land": True, "land_owned_before_2019_02_01": True,
                 "is_institutional_landholder": False, "family_income_tax_payer": False,
                 "family_constitutional_or_elected_post": False, "family_govt_employee_excluded": False,
                 "family_monthly_pension_inr": 0, "family_registered_professional": False,
                 "family_member_already_receiving": False, "ekyc_done": False},
        records={"aadhaar": {"name": "Sunil Patil"}, "land_record": {"name": "Suneel Patil"}},
        status="eKYC pending", scheme_id="pm_kisan")
    assert out["preflight"]["fixes"][0]["action"] == "fix_name_mismatch"
    assert out["status"]["matched"] == "ekyc pending"
    assert out["status"]["causes"][0]["action"] == "fix_name_mismatch"

"""Benefit valuation and scheme ranking: the part that decides what a person is told first."""
import pytest

from core.benefit import amounts_in, benefit_of, is_personal
from core.eligibility import evaluate_scheme
from core.ranking import lesser_known, match_strength, rank_schemes, value_points

BASE = {"id": "s", "name": "Test Scheme", "source_url": "http://x", "last_verified": "2026-01-01",
        "rank": {"value_score": 3, "effort": 3}, "origin": "discovered", "verified": False,
        "eligibility": {"all": []}, "actions": {}, "status_map": {}, "documents": []}


def scheme(**over):
    return {**BASE, **over}


# ---------- reading amounts out of the page text ----------

@pytest.mark.parametrize("text,expected", [
    ("Rs. 1,01,000 in installments", [101000]),
    ("₹15,000 from the Cess Fund", [15000]),
    ("50 percent capital subsidy of Rs. 30.00 lakhs", [3000000]),
    ("assistance of 2 crore for the village", [20000000]),
    ("no money mentioned here", []),
])
def test_amounts_in(text, expected):
    assert amounts_in(text) == expected


# ---------- what the benefit is really worth in a year ----------

def test_monthly_payment_becomes_a_year():
    b = benefit_of(scheme(benefit_summary="Scholarship of Rs 60 per month for 10 months"))
    assert b.annual_value == 600 and b.kind == "cash"


def test_annual_field_is_never_multiplied_again():
    """Rs 200 a month is stored as Rs 2,400 a year; reading "monthly" must not make it Rs 28,800."""
    b = benefit_of(scheme(benefit_annual_inr=2400,
                          benefit_summary="Monthly pension: central share Rs 200 (age 60-79)"))
    assert b.annual_value == 2400


def test_one_time_grant_is_spread_not_counted_as_income():
    once = benefit_of(scheme(benefit_summary="One time lump sum financial assistance of Rs. 20,000/-"))
    assert once.annual_value == 6666 and "one-time" in once.basis
    # a house grant is a single payment even when the page never says "one-time"
    house = benefit_of(scheme(benefit_annual_inr=130000,
                              benefit_summary="Financial assistance for construction of houses for the poor"))
    assert house.annual_value == 43333


def test_insurance_cover_is_not_cash_income():
    b = benefit_of(scheme(benefit_summary="Free hospital treatment up to Rs 5 lakh per family per year"))
    assert b.kind == "insurance" and 10_000 <= b.annual_value <= 30_000
    assert "500,000" in b.basis


def test_spread_until_the_child_turns_18():
    b = benefit_of(scheme(benefit_summary="Rs 1,01,000 in installments from birth until the girl "
                                          "reaches 18 years of age"))
    assert b.annual_value == pytest.approx(101000 / 18, abs=1)


def test_money_for_a_village_or_institution_is_worth_nothing_to_a_person():
    village = scheme(name="Integrated development of SC majority villages",
                     benefit_summary="Rs 20,00,000 per village for convergent implementation")
    assert not is_personal(village) and benefit_of(village).annual_value == 0
    award = scheme(name="Incentive award to Panchayati Raj Institutions",
                   benefit_summary="Cash award of Rs 25,000 for organizations")
    assert not is_personal(award)
    # a family benefit on the same kind of page is still personal
    assert is_personal(scheme(benefit_summary="Rs 5,000 to the beneficiary family of the village"))


def test_free_service_without_an_amount_still_counts():
    b = benefit_of(scheme(rank={"value_score": 5, "effort": 2},
                          benefit_summary="Free accommodation, food, bedding and textbooks"))
    assert b.annual_value > 0 and b.kind == "in_kind"


def test_value_points_flatten_large_amounts():
    assert value_points(0) == 0
    assert value_points(6_000) < value_points(60_000) < value_points(600_000)
    assert value_points(10_000_000) <= 6.0          # capped: ten times more is not ten times better


# ---------- how well a scheme targets this person ----------

def test_exclusions_do_not_count_as_targeting():
    """Passing "you are not an income-tax payer" does not mean the scheme is aimed at you."""
    targeted = scheme(eligibility={"all": [
        {"field": "social_category", "op": "eq", "value": "sc", "reason": "SC only"},
        {"field": "marital_status", "op": "eq", "value": "widowed", "reason": "widows"}]})
    exclusions = scheme(eligibility={"all": [
        {"field": "family_income_tax_payer", "op": "eq", "value": False, "reason": "no tax payer"},
        {"field": "family_govt_employee_excluded", "op": "eq", "value": False, "reason": "no govt job"}]})
    profile = {"social_category": "sc", "marital_status": "widowed",
               "family_income_tax_payer": False, "family_govt_employee_excluded": False}
    assert match_strength(evaluate_scheme(targeted, profile))[0] == 4.0      # two identity rules
    assert match_strength(evaluate_scheme(exclusions, profile))[0] == 0.0


def test_scope_rules_do_not_count_as_targeting():
    s = scheme(state="maharashtra", district="beed", eligibility={"all": [
        {"field": "state", "op": "eq", "value": "maharashtra", "reason": "state"}]})
    assert match_strength(evaluate_scheme(s, {"state": "maharashtra", "district": "beed"}))[0] == 0.0


def test_unread_conditions_weaken_the_match():
    rules = [{"field": "gender", "op": "eq", "value": "female", "reason": "women"}]
    plain = evaluate_scheme(scheme(eligibility={"all": rules}), {"gender": "female"})
    murky = evaluate_scheme(scheme(eligibility={"all": rules},
                                   unmapped_criteria=["must own a sewing machine"]), {"gender": "female"})
    assert match_strength(murky)[0] < match_strength(plain)[0]


# ---------- the order the person actually sees ----------

def _rank(catalog, profile):
    results = [evaluate_scheme(s, profile) for s in catalog.values()]
    ranking = rank_schemes(results, catalog)
    return [ranking.start_with.scheme_id] + [r.scheme_id for r in ranking.queue], ranking


def test_bigger_real_benefit_wins_between_two_matching_schemes():
    small = scheme(id="small", name="Small Cash", benefit_annual_inr=600,
                   benefit_summary="Rs 600 per year", eligibility={"all": [
                       {"field": "is_bpl", "op": "eq", "value": True, "reason": "BPL"}]})
    big = scheme(id="big", name="Big Cash", benefit_annual_inr=36000,
                 benefit_summary="Rs 3,000 per month", eligibility={"all": [
                     {"field": "is_bpl", "op": "eq", "value": True, "reason": "BPL"}]})
    order, _ = _rank({"small": small, "big": big}, {"is_bpl": True})
    assert order[0] == "big"


def test_a_scheme_aimed_at_this_person_beats_a_generic_one_of_similar_value():
    generic = scheme(id="generic", name="Generic", benefit_annual_inr=9000,
                     benefit_summary="Rs 9,000 per year", eligibility={"all": [
                         {"field": "is_bpl", "op": "eq", "value": True, "reason": "BPL"}]})
    aimed = scheme(id="aimed", name="Aimed", benefit_annual_inr=9000,
                   benefit_summary="Rs 9,000 per year", eligibility={"all": [
                       {"field": "is_bpl", "op": "eq", "value": True, "reason": "BPL"},
                       {"field": "social_category", "op": "eq", "value": "st", "reason": "ST only"},
                       {"field": "disability_percent", "op": "gte", "value": 40, "reason": "disabled"}]})
    profile = {"is_bpl": True, "social_category": "st", "disability_percent": 60}
    order, _ = _rank({"generic": generic, "aimed": aimed}, profile)
    assert order[0] == "aimed"


def test_village_money_never_outranks_a_persons_scheme():
    village = scheme(id="village", name="Village Development Grant",
                     benefit_summary="Rs 20,00,000 per village for infrastructure",
                     eligibility={"all": [{"field": "is_bpl", "op": "eq", "value": True, "reason": "BPL"}]})
    person = scheme(id="person", name="Widow Pension", benefit_annual_inr=12000,
                    benefit_summary="Rs 1,000 per month to the beneficiary",
                    eligibility={"all": [{"field": "is_bpl", "op": "eq", "value": True, "reason": "BPL"}]})
    order, _ = _rank({"village": village, "person": person}, {"is_bpl": True})
    assert order[0] == "person"


def test_ranking_explains_itself():
    s = scheme(id="s", benefit_annual_inr=6000, benefit_summary="Rs 6,000 per year",
               eligibility={"all": [{"field": "social_category", "op": "eq", "value": "sc", "reason": "SC"}]})
    _, ranking = _rank({"s": s}, {"social_category": "sc"})
    reasons = " ".join(ranking.scored["s"].reasons)
    assert "meet 1 of its conditions" in reasons and "6,000 a year" in reasons
    assert "not yet checked by a person" in reasons


def test_lesser_known_prefers_obscure_schemes_of_similar_worth():
    flagship = scheme(id="pm", name="PM-KISAN Samman Nidhi", benefit_annual_inr=6000,
                      benefit_summary="Rs 6,000 per year", eligibility={"all": [
                          {"field": "is_bpl", "op": "eq", "value": True, "reason": "BPL"}]})
    obscure = scheme(id="zp", name="ZP Cess Fund Assistance", agency_type="district",
                     benefit_annual_inr=6000, benefit_summary="Rs 6,000 per year",
                     eligibility={"all": [{"field": "is_bpl", "op": "eq", "value": True, "reason": "BPL"}]})
    catalog = {"pm": flagship, "zp": obscure}
    results = [evaluate_scheme(s, {"is_bpl": True}) for s in catalog.values()]
    assert [r.scheme_id for r in lesser_known(results, catalog)] == ["zp"]

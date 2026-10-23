from core import apply_answer, check_eligibility, next_best_question
from core.loader import load_questions

QUESTIONS = {q["id"]: q for q in load_questions()}

# Simulated user who answers truthfully from a hidden profile.
TRUTH = {"age": 64, "owns_cultivable_land": True, "land_owned_before_2019_02_01": True,
         "family_member_already_receiving": False,
         "is_bpl": True, "in_pmjay_beneficiary_list": False, "exclusions": False}


def _answer(q):
    if q["id"] == "exclusions":
        return TRUTH["exclusions"]
    return TRUTH[q["fields"][0]]


def test_first_question_is_most_useful():
    q = next_best_question({})
    assert q["id"] == "age"          # age appears in two schemes


def test_conversation_finishes_within_seven_questions():
    profile, asked = {}, 0
    while (q := next_best_question(profile)) is not None:
        profile = apply_answer(profile, q, _answer(q))
        asked += 1
        assert asked <= 7, f"too many questions: {profile.get('_asked')}"
    statuses = {r.scheme_id: r.status for r in check_eligibility(profile)}
    assert statuses == {"pm_kisan": "eligible", "ignoaps": "eligible", "pmjay": "ineligible"}


def test_land_questions_skipped_for_landless():
    profile = apply_answer({}, QUESTIONS["owns_land"], False)
    profile = apply_answer(profile, QUESTIONS["age"], 30)
    q = next_best_question(profile)
    assert q is None or q["id"] not in {"land_before_2019", "exclusions", "already_receiving"}


def test_combined_yes_marks_followup_and_does_not_loop():
    profile = {"owns_cultivable_land": True}
    profile = apply_answer(profile, QUESTIONS["exclusions"], True)
    assert profile["_needs_followup"] == "exclusions"
    q = next_best_question(profile)
    assert q is None or q["id"] != "exclusions"

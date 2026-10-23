"""Scheme Saathi core logic: deterministic, testable, no LLM inside."""
from .checklist import build_checklist
from .eligibility import check_eligibility
from .name_match import compare_dob, compare_names
from .pipeline import assist
from .preflight import preflight_check
from .questions import apply_answer, next_best_question
from .ranking import rank_schemes
from .status_decoder import decode_status

__all__ = ["assist", "check_eligibility", "rank_schemes", "next_best_question", "apply_answer",
           "compare_names", "compare_dob", "preflight_check", "decode_status", "build_checklist"]

"""Reason-based questions: code scores which unknown fact is worth most (core.questions.question_candidates),
the planner LLM picks one of those candidates and explains why in the user's language.

The LLM can only choose among the candidates code offers; anything else is replaced by the top one.
"""
from __future__ import annotations

import json

from core.fields import load_fields
from core.questions import question_candidates
from llm import LLMClient, LLMError

from .explain import LANG_NAMES, plain

MAX_QUESTIONS = 6
MIN_SCORE = 1.0

CHOOSE_SYSTEM = """You decide the single most useful next question to ask a person looking for government
schemes. You get their situation and candidate questions, each with a score (higher = settles more,
more valuable and less-known schemes) and the schemes it would settle. Pick ONE candidate field. You may
prefer a lower score if it clearly unlocks an important need. Then write:
- question: one short, kind question in {language}, in plain words (no codes like agricultural_labourer)
- reason: one sentence in {language} telling them why you ask, naming one or two of the schemes."""

CHOOSE_SCHEMA = {
    "type": "object",
    "properties": {"field": {"type": "string"}, "question": {"type": "string"}, "reason": {"type": "string"}},
    "required": ["field", "question", "reason"],
}


def _shape(c: dict, question: str, reason: str) -> dict:
    spec = load_fields()[c["field"]]
    return {"field": c["field"], "qid": c["question"]["id"], "question": question, "reason": reason,
            "type": spec["type"],
            "choices": list(spec.get("values") or []), "schemes": c["schemes"][:5], "score": c["score"],
            "text_en": c["question"]["text_en"], "fields": [c["field"]]}


def next_question(planner: LLMClient | None, lang: str, profile: dict, catalog: dict,
                  situation: str = "") -> dict | None:
    if len(profile.get("_asked", [])) >= MAX_QUESTIONS:
        return None
    cands = [c for c in question_candidates(profile, catalog) if c["score"] >= MIN_SCORE]
    if not cands:
        return None
    top = cands[0]
    default_q = top["question"].get(f"text_{lang}") or top["question"]["text_en"]
    default_reason = f"This decides {len(top['schemes'])} scheme(s), e.g. {', '.join(top['schemes'][:2])}."
    if planner is None:
        return _shape(top, default_q, default_reason)
    payload = {"situation": situation,
               "candidates": [{"field": c["field"], "about": load_fields()[c["field"]]["description"],
                               "score": c["score"], "settles": c["schemes"][:4]} for c in cands]}
    try:
        r = planner.complete([{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                             system=CHOOSE_SYSTEM.format(language=LANG_NAMES.get(lang, "simple English")),
                             json_schema=CHOOSE_SCHEMA, max_tokens=600).data
    except LLMError:
        return _shape(top, default_q, default_reason)
    chosen = next((c for c in cands if c["field"] == r["field"]), None)
    if chosen is None:                      # the LLM may only pick from the candidates code offered
        return _shape(top, default_q, default_reason)
    return _shape(chosen, plain(r["question"]), plain(r["reason"]))

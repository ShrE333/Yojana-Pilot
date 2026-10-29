"""Conversational beta: free-text profile extraction, deterministic follow-up selection.

No official application submission or personal identification data collected.
In-memory sessions are DEMO ONLY. Persist in Postgres before deployment.
"""
from __future__ import annotations

import time
import uuid
import re
from threading import RLock
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.fields import validate_profile
from core.questions import question_candidates
from discovery.catalog import load_catalog
from saathi.shortlist import build_shortlist
from saathi.understand import understand
from llm import get_llm

router = APIRouter(tags=["Conversation Beta"])
_lock = RLock()
_sessions: dict[str, dict] = {}
_MAX_SESSIONS = 100
_TTL = 3600

# Never forward common sensitive identifiers to a hosted model.
SENSITIVE = [
    (re.compile(r"(?<!\d)(?:\d[ -]?){11}\d(?!\d)"), "12-digit identity number"),
    (re.compile(r"(?<!\d)\d{9,18}(?!\d)"), "possible bank/account number"),
]

QUESTION_TEXT = {
    "is_student": {"hi": "क्या आप अभी पढ़ाई कर रहे हैं?", "mr": "तुम्ही सध्या शिक्षण घेत आहात का?"},
    "occupation": {"hi": "आप अभी क्या काम करते हैं?", "mr": "तुम्ही सध्या कोणते काम करता?"},
    "annual_family_income_inr": {"hi": "आपके परिवार की सालाना आय कितनी है?", "mr": "तुमच्या कुटुंबाचे वार्षिक उत्पन्न किती आहे?"},
    "state": {"hi": "आप किस राज्य में रहते हैं?", "mr": "तुम्ही कोणत्या राज्यात राहता?"},
    "district": {"hi": "आप किस जिले में रहते हैं?", "mr": "तुम्ही कोणत्या जिल्ह्यात राहता?"},
    "owns_cultivable_land": {"hi": "क्या आपके परिवार के पास खेती की जमीन है?", "mr": "तुमच्या कुटुंबाकडे शेतीची जमीन आहे का?"},
}

def _safe_message(text: str) -> str:
    for pattern, kind in SENSITIVE:
        if pattern.search(text):
            raise HTTPException(422, f"Please remove the {kind} before sending your message. Do not share Aadhaar, bank or phone numbers.")
    return text.strip()

def _question_text(q: dict, lang: str) -> str:
    localized = QUESTION_TEXT.get(q["field"], {}).get(lang)
    return localized or q["text"]


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=1800)
    lang: str = Field(default="en", pattern="^(en|hi|mr)$")
    session_id: str | None = Field(default=None, max_length=64)


def _store(session_id, state):
    with _lock:
        now = time.time()
        for key, val in list(_sessions.items()):
            if now - val["updated"] > _TTL:
                _sessions.pop(key, None)
        if len(_sessions) >= _MAX_SESSIONS and session_id not in _sessions:
            raise HTTPException(503, "Demo is at capacity; try later")
        state["updated"] = now
        _sessions[session_id] = state


def _shortlist_public(profile, catalog):
    cards = build_shortlist({**profile, "_asked": []}, catalog)
    return cards


def _next_question(profile, catalog, asked):
    candidates = question_candidates({**profile, "_asked": asked}, catalog, limit=5)
    # Never infer whether someone is a student from their age. Question must have
    # positive relevance to a genuinely undecided eligibility rule.
    for c in candidates:
        q = c["question"]
        if q["id"] not in asked and c["score"] > 0:
            return {"field": c["field"], "text": q.get("text_en") or c["field"],
                    "reason": "This detail affects eligibility for: " + ", ".join(c["schemes"][:2]),
                    "choices": q.get("choices", []), "type": q.get("type", "text"),
                    "qid": q["id"], "fields": q["fields"], "text_en": q.get("text_en", "")}
    return None


@router.post("/api/v2/conversation")
def conversation(body: Message):
    # A new conversation requires no form, just a message.
    if body.session_id:
        with _lock:
            state = _sessions.get(body.session_id)
            if not state or time.time() - state["updated"] > _TTL:
                raise HTTPException(404, "Session expired; start a new conversation")
            state = {**state, "profile": dict(state["profile"]), "asked": list(state["asked"])}
        sid = body.session_id
    else:
        sid = uuid.uuid4().hex
        state = {"profile": {}, "asked": [], "pending": None, "lang": body.lang,
                 "updated": time.time()}

    text = _safe_message(body.text)

    try:
        llm = get_llm("extractor")
        parsed = understand(llm, text, state["pending"])
    except Exception as exc:
        # Don't falsely record a fact if the hosted service is down.
        raise HTTPException(503, "Language service unavailable. Check the model configuration.") from exc

    cleaned, _ = validate_profile(parsed.facts)
    # A changed fact is not silently accepted; citizens can correct it explicitly.
    changes = {k: {"previous": state["profile"][k], "new": v} for k, v in cleaned.items()
               if k in state["profile"] and state["profile"][k] != v}
    state["profile"].update(cleaned)
    state["lang"] = parsed.language if parsed.language in ("en", "hi", "mr") else body.lang
    if state["pending"] is not None and not cleaned:
        # If the user did not answer, keep the question active, instead of
        # marking another scheme field as asked and advancing the checklist.
        previous = state["pending"]
        if parsed.intent in ("dont_know",):
            state["pending"] = None
        elif parsed.intent in ("greeting", "other"):
            _store(sid, state)
            return {"session_id": sid, "message": _question_text(previous, state["lang"]),
                    "question": {k: previous[k] for k in ("field", "text", "reason", "choices", "type")},
                    "profile": state["profile"], "results": _shortlist_public(state["profile"], load_catalog()),
                    "corrections": changes, "disclaimer": "Recommendations are informational; verify against official sources."}
    catalog = load_catalog()
    q = _next_question(state["profile"], catalog, state["asked"])
    if q:
        state["asked"].append(q["qid"])
        state["pending"] = q
        answer = _question_text(q, state["lang"])
    else:
        state["pending"] = None
        answer = "I found possible schemes based on what you shared. Review the sources and verification notices below."
    _store(sid, state)
    shortlist = _shortlist_public(state["profile"], catalog)
    return {"session_id": sid, "message": answer, "question": {k: q[k] for k in
            ("field", "text", "reason", "choices", "type") } if q else None,
            "corrections": changes,
            "profile": state["profile"], "results": shortlist,
            "disclaimer": "Recommendations are informational; verify unreviewed schemes against official sources."}


@router.delete("/api/v2/conversation/{session_id}")
def clear_conversation(session_id: str):
    with _lock:
        _sessions.pop(session_id, None)
    return {"deleted": True}

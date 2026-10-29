"""Conversational beta: free-text profile extraction, deterministic follow-up selection.

No official application submission or personal identification data collected.
DATABASE_URL enables persistent cases. Without it, development sessions use RAM.
"""
from __future__ import annotations

import time
import os
import uuid
import re
from threading import RLock
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from core.fields import validate_profile
from core.questions import question_candidates
from discovery.catalog import load_catalog
from saathi.shortlist import build_shortlist
from saathi.understand import understand
from llm import get_llm
from . import case_store, inference
from .access import owner, issue, require_memory
from .privacy import redact
from core.relevance import intent_from_text
from core.languages import normalize, ROWS
from pydantic import field_validator
from core.assessment import relevant_catalog, decide, conclusion, final_answer
from .metrics import record

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
    if re.search(r'(?:#|\b)handoff=[A-Za-z0-9_-]{32,100}',text):
        raise HTTPException(422,'Do not paste private voice invitation links into messages.')
    for pattern, kind in SENSITIVE:
        if pattern.search(text):
            raise HTTPException(422, f"Please remove the {kind} before sending your message. Do not share Aadhaar, bank or phone numbers.")
    return text.strip()

def _question_text(q: dict, lang: str) -> str:
    localized = QUESTION_TEXT.get(q["field"], {}).get(lang) or (q.get('text_'+lang) if not q.get('synthetic') else None)
    return localized or q["text"]


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=1800)
    lang: str = 'en'
    language_override: bool = False
    refine: bool = False
    session_id: str | None = Field(default=None, max_length=64)
    request_id: str | None = Field(default=None, min_length=8, max_length=80)

    @field_validator('lang')
    @classmethod
    def language(cls, value):
        if value not in {'auto'} | {c for c,_ in ROWS} | {c+'-IN' for c,_ in ROWS}:
            raise ValueError('Unsupported language code')
        return normalize(value)


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
                    "qid": q["id"], "fields": q["fields"], "text_en": q.get("text_en", ""),
                    'text_hi':q.get('text_hi'),'text_mr':q.get('text_mr'),'synthetic':q.get('synthetic',False)}
    return None


def conversation(body: Message, access_token=None, trusted=False, tx=None):
    if tx is not None:
        tx.lock(body.session_id)
        return _conversation(body, access_token, trusted, tx)
    if case_store.enabled():
        with case_store.transaction(body.session_id or 'new-conversation') as transaction:
            return _conversation(body, access_token, trusted, transaction)
    with _lock:
        return _conversation(body, access_token, trusted, None)


def _conversation(body, access_token, trusted, tx):
    if tx and not body.session_id and body.request_id and access_token:
        candidate = case_store.digest(access_token + ':' + body.request_id)[:32]
        if tx.execute('SELECT id FROM case_sessions WHERE id=?', (candidate,)).fetchone():
            body = body.model_copy(update={'session_id': candidate})
    # A new conversation requires no form, just a message.
    if body.session_id:
        with _lock:
            row = tx.session(body.session_id, access_token, trusted) if tx else None
            if row and row['status'] == 'closed':
                raise HTTPException(409, 'Case is closed. Reopen it before continuing.')
            state = row['state'] if row else _sessions.get(body.session_id)
            if not state or time.time() - state["updated"] > _TTL:
                if not tx:
                    raise HTTPException(404, "Session expired; start a new conversation")
            if not tx and not trusted:
                require_memory(state, access_token)
            state = {**state, "profile": dict(state["profile"]), "asked": list(state["asked"])}
        sid = body.session_id
    else:
        sid = case_store.digest(access_token + ':' + body.request_id)[:32] if tx and access_token and body.request_id else uuid.uuid4().hex
        state = {"profile": {}, "asked": [], "pending": None, "lang": body.lang,
                 "updated": time.time(), "owner_hash": case_store.digest(access_token or '')}

    if body.request_id and state.get('last_request_id') == body.request_id:
        if state.get('request_hash') != case_store.digest(body.text):
            raise HTTPException(409, 'Request ID already used for a different message')
        return state['last_response']

    original_profile = dict(state['profile'])
    if body.refine:
        state.update(asked=[],pending=None,question_count=0,pending_repeats=0)

    def finish(result):
        state['last_response']=result
        history_limit = max(2, min(int(os.getenv('CASE_HISTORY_LIMIT', '40')), 200))
        state['history'] = (state.get('history', []) + [
            {'role': 'user', 'content': redact(body.text)},
            {'role': 'assistant', 'content': redact(result['message'])}])[-history_limit:]
        if body.request_id:
            state['last_request_id'], state['last_response'] = body.request_id, result
            state['request_hash'] = case_store.digest(body.text)
        state['updated'] = time.time()
        if tx:
            tx.save(sid, state, access_token)
            if state['profile'] != original_profile:
                tx.execute("UPDATE case_sessions SET status='open' WHERE id=?", (sid,))
            tx.message(sid, 'user', body.text)
            tx.message(sid, 'assistant', result['message'])
        else:
            _store(sid, state)
        return result

    text = _safe_message(body.text)
    state['intent_context'] = intent_from_text(text, state.get('intent_context'))

    started=time.perf_counter()
    try:
        llm = get_llm("extractor")
        parsed = understand(llm, text, state["pending"],
                            {'facts': state['profile'], 'recent_messages': state.get('history', [])[-10:]})
        inference.succeeded(llm)
        record('inference',time.perf_counter()-started)
    except Exception as exc:
        # Don't falsely record a fact if the hosted service is down.
        code = inference.failure(exc)
        record('inference',time.perf_counter()-started,'error')
        raise HTTPException(429 if code == 'rate_limited_or_quota_exhausted' else 503,
                            {'code': code, 'message': 'Language service unavailable. Check the model configuration.'}) from exc

    cleaned, _ = validate_profile(parsed.facts)
    if getattr(parsed,'requested_category',None) in ('pension','income','education','agriculture','health','housing','employment'):
        state['intent_context']['categories'] = [parsed.requested_category]
    # A changed fact is not silently accepted; citizens can correct it explicitly.
    changes = {k: {"previous": state["profile"][k], "new": v} for k, v in cleaned.items()
               if k in state["profile"] and state["profile"][k] != v}
    state["profile"].update(cleaned)
    if state.get('assessment',{}).get('assessment_complete') and (state['profile']!=original_profile or getattr(parsed,'requested_category',None)):
        state.update(asked=[],pending=None,question_count=0,pending_repeats=0)
    detected = normalize(parsed.language)
    state['lang'] = body.lang if body.language_override and body.lang != 'auto' else (detected if detected != 'auto' else 'en')
    context_profile = {**state['profile'], '_intent': state['intent_context']}
    catalog = relevant_catalog(context_profile,load_catalog())
    shortlist = _shortlist_public(context_profile, catalog) if catalog else {'start_with':None,'others':[],'needs_information':[],'schemes_checked':0}
    count=state.get('question_count',len(state['asked']))
    decision=decide({**context_profile,'_asked':state['asked']},catalog,shortlist,count,getattr(parsed,'assessment_complete',False))
    q=None
    previous=state.get('pending')
    if (not decision['assessment_complete'] and previous and not cleaned
            and parsed.intent in ('greeting','other') and state.get('pending_repeats',0)<1):
        q=previous;state['pending_repeats']=state.get('pending_repeats',0)+1
    elif not decision['assessment_complete'] and decision['next_candidate']:
        c=decision['next_candidate'];question=c['question']
        q={'field':c['field'],'text':question.get('text_en') or c['field'],
           'reason':'This detail affects eligibility for: '+', '.join(c['schemes'][:2]),
           'choices':question.get('choices',[]),'type':question.get('type','text'),
           'qid':question['id'],'fields':question['fields'],'text_en':question.get('text_en',''),
           'text_hi':question.get('text_hi'),'text_mr':question.get('text_mr'),'synthetic':question.get('synthetic',False)}
        state['pending_repeats']=0
    if q:
        if q['qid'] not in state['asked']:
            state["asked"].append(q["qid"])
        state['question_count']=count+1
        state["pending"] = q
        answer = _question_text(q, state["lang"])
    else:
        state["pending"] = None
        answer = final_answer(decision)
    decision.pop('next_candidate',None)
    decision['question_count']=state.get('question_count',count)
    state['assessment']=decision
    return finish({"session_id": sid, "message": answer, "question": {k: q[k] for k in
            ("field", "text", "reason", "choices", "type") } if q else None,
            "corrections": changes,
            "profile": state["profile"], "results": shortlist, 'language': state['lang'],
            'assessment_complete':decision['assessment_complete'],'assessment':decision,
            'recommendations':decision['recommendations'],'spoken_text':answer[:360] if q else conclusion(decision['recommendations']),
            'text_fallback': state['lang'] not in ('en','hi','mr'),
            "disclaimer": "Recommendations are informational; verify unreviewed schemes against official sources."})


@router.post('/api/v2/conversation')
def conversation_http(body: Message, request: Request, response: Response):
    token = owner(request)
    if not body.session_id:
        token = issue(request, response)
    return conversation(body, access_token=token)


@router.delete("/api/v2/conversation/{session_id}")
def clear_conversation(session_id: str, request: Request = None):
    token = owner(request) if request else None
    if case_store.enabled():
        with case_store.transaction(session_id) as tx:
            tx.session(session_id, token)
            tx.execute('DELETE FROM case_sessions WHERE id=?', (session_id,))
        return {'deleted': True}
    with _lock:
        if session_id in _sessions:
            require_memory(_sessions[session_id], token)
        _sessions.pop(session_id, None)
    return {"deleted": True}

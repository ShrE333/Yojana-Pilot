"""Focused V2.1 regression tests. Existing repository tests must also pass."""
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from web import conversation_api as api


def test_sensitive_12_digit_id_rejected():
    with pytest.raises(HTTPException) as e:
        api._safe_message('My Aadhaar is 1234 5678 9012')
    assert e.value.status_code == 422


def test_benign_income_not_rejected():
    assert api._safe_message('I am 60 years old and earn 40000 per year')


def test_localized_question():
    q = {'field': 'occupation', 'text': 'What do you do?'}
    assert 'काम' in api._question_text(q, 'hi')
    assert api._question_text(q, 'en') == 'What do you do?'


def test_detects_correction_and_preserves_profile(monkeypatch):
    api._sessions.clear()
    monkeypatch.setattr(api, 'get_llm', lambda role: object())
    monkeypatch.setattr(api, 'understand', lambda *_: SimpleNamespace(facts={'age': 61}, language='en', intent='give_info'))
    monkeypatch.setattr(api, 'load_catalog', lambda: {})
    monkeypatch.setattr(api, '_next_question', lambda *_: None)
    monkeypatch.setattr(api, '_shortlist_public', lambda *_: {})
    first = api.conversation(api.Message(text='I am 61'))
    sid = first['session_id']
    monkeypatch.setattr(api, 'understand', lambda *_: SimpleNamespace(facts={'age': 62}, language='en', intent='give_info'))
    second = api.conversation(api.Message(text='Correction I am 62', session_id=sid))
    assert second['profile']['age'] == 62
    assert second['corrections']['age'] == {'previous': 61, 'new': 62}
    api._sessions.clear()

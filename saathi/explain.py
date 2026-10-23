"""LLM step [F]: write replies in the user's language, only from engine output.

The agent never sees raw web pages and never decides eligibility. It gets the JSON
that core/ produced and turns it into short, plain sentences.
"""
from __future__ import annotations

import json
import re

from llm import LLMClient


def plain(text: str) -> str:
    """Terminals and WhatsApp do not render markdown: drop bold markers and heading hashes."""
    text = text.replace("**", "").replace("__", "")
    return re.sub(r"(?m)^\s*#{1,6}\s*", "", text).strip()

LANG_NAMES = {"hi": "Hindi (Devanagari script)", "mr": "Marathi (Devanagari script)", "en": "simple English"}

RULES = """You are Scheme Saathi, a helper for people in rural India looking for government welfare schemes.
Hard rules:
- Use ONLY the JSON you are given. Never add a scheme, amount, rule, document or link that is not in it.
- Never say a person is or is not eligible unless the JSON status says so. Status meanings:
  eligible = meets every rule we know; eligible_check_manually = meets the rules we could read, but
  the listed points must be checked by them; near_miss = almost (say what is missing); check_manually =
  rules could not be read, they must read the official page.
- For any scheme with verified=false say it was found on <source_site> and not yet checked by a person.
- When you say why a scheme is first, use its why_ranked lines and annual_value_inr (rupees a year
  this person could expect). Never invent an amount or compare schemes on numbers not in the JSON.
- Explain why a scheme fits ONLY with its rules_met and the profile. Never assume anything about the
  person that is not in the JSON (do not say they are a student, single child, etc. unless stated).
- Always give the source link for a scheme you recommend.
- Never ask for Aadhaar, bank account or phone numbers.
- Short sentences. Plain text only: no tables, no **bold**, no # headings. Simple numbered or dashed lists are fine.
- Reply in {language}."""


def _chat(llm: LLMClient, lang: str, task: str, payload: dict, history: list[dict] | None = None,
          max_tokens: int = 900) -> str:
    system = RULES.format(language=LANG_NAMES.get(lang, "simple English")) + "\n\nTask: " + task
    msgs = list(history or [])[-6:]
    msgs.append({"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)})
    return plain(llm.complete(msgs, system=system, max_tokens=max_tokens, temperature=0.2).text)


def phrase_question(llm: LLMClient, lang: str, question: dict, ack: str | None = None) -> str:
    task = ("Ask the person exactly this one question, briefly and politely. If 'acknowledge' is given, "
            "first thank them in a few words for what they told. If choices are given, mention a few as examples "
            "in plain words of their language; never show codes like class_1_to_8 or agricultural_labourer.")
    return _chat(llm, lang, task, {"question": question["text_en"], "choices": question.get("choices", [])[:8],
                                   "acknowledge": ack}, max_tokens=300)


def explain_shortlist(llm: LLMClient, lang: str, shortlist: dict) -> str:
    task = ("Present the shortlist. 1) Recommend start_with first: why it fits, the benefit, documents, how to "
            "apply, source link. 2) Then list 'others' one line each (name + benefit + source site). 3) Near misses "
            "in one line each, saying what is missing. 4) check_on_official_page: one line each, 'may apply, check "
            "the official page', with the link. 5) If start_with is null, say no scheme could be confirmed yet "
            "and mention the closest options. End by asking if they want details on any scheme. Under 250 words.")
    return _chat(llm, lang, task, shortlist, max_tokens=1500)


def answer(llm: LLMClient, lang: str, question: str, context: dict, history: list[dict]) -> str:
    task = ("Answer the person's latest question using only the context JSON (their profile, the shortlist and "
            "full details of any scheme they asked about). If the answer is not in the context, say you do not "
            "know and point to the official source link. Under 150 words.")
    return _chat(llm, lang, task, {"person_asks": question, "context": context}, history, max_tokens=1000)

"""The conversation: ask a few questions, search official sites, decide, explain, answer follow-ups.

    bot = Saathi()
    print(bot.greeting())
    reply = bot.handle("मेरी उम्र 62 है ...")

Stages:
  profiling  ask the basic questions (state, age, gender, work, income, category) that are missing
  (search)   discover schemes on official websites for this profile, add them to the catalog
  refining   ask up to MAX_REFINE more questions chosen by core.next_best_question over the catalog
  chat       show the shortlist, then answer questions; new facts re-run the engine

LLM: understand() reads every message (extractor role); explain.* writes every reply (agent role).
Code: validation, eligibility, ranking and question choice. The LLM never decides eligibility.
"""
from __future__ import annotations

from typing import Callable

from core.loader import load_questions
from core.questions import next_best_question, synthetic_questions
from discovery.catalog import load_catalog
from llm import LLMClient, get_llm

from . import explain
from .shortlist import build_shortlist, find_scheme, scheme_details
from .understand import understand

BASIC_QUESTIONS = ["state", "age", "gender", "occupation", "income", "category"]
MAX_REFINE = 4

GREETING = (
    "Namaste! I am Scheme Saathi. I find government schemes you may qualify for, from official websites.\n"
    "नमस्ते! मैं स्कीम साथी हूँ। मैं सरकारी वेबसाइटों से आपके लिए योजनाएँ ढूँढता हूँ।\n"
    "नमस्कार! मी स्कीम साथी. मी सरकारी संकेतस्थळांवरून तुमच्यासाठी योजना शोधतो.\n\n"
    "Tell me about yourself in any language: age, state, work, family income, category.\n"
    "I will never ask for your Aadhaar number. Type 'quit' to exit, 'restart' to start again."
)
SEARCHING = {
    "en": "Thank you. Searching official government websites for schemes that may fit you. This can take a minute...",
    "hi": "धन्यवाद। आपके लिए सरकारी वेबसाइटों पर योजनाएँ खोज रहा हूँ। इसमें एक मिनट लग सकता है...",
    "mr": "धन्यवाद. तुमच्यासाठी सरकारी संकेतस्थळांवर योजना शोधत आहे. याला एक मिनिट लागू शकतो...",
}


class Saathi:
    def __init__(self, extractor: LLMClient | None = None, agent: LLMClient | None = None,
                 discoverer=None, web: bool = True, out: Callable[[str], None] = print,
                 catalog_loader: Callable[[], dict] = load_catalog,
                 max_searches: int = 6, max_pages: int = 10):
        self.extractor = extractor or get_llm("extractor")
        self.agent = agent or get_llm("agent")
        self._discoverer = discoverer
        self.web = web
        self.out = out
        self.catalog_loader = catalog_loader
        self.max_searches, self.max_pages = max_searches, max_pages
        base = load_questions()
        self.questions = {q["id"]: q for q in list(base) + synthetic_questions(base)}
        self.reset()

    # ---------- state ----------
    def reset(self) -> None:
        self.lang = "en"
        self.profile: dict = {"_asked": []}
        self.stage = "profiling"
        self.pending: dict | None = None
        self.refine_asked = 0
        self.history: list[dict] = []
        self.shortlist: dict | None = None
        self.catalog = self.catalog_loader()
        self.discovery_report = None

    @property
    def discoverer(self):
        if self._discoverer is None:
            from discovery.discover import Discoverer
            self._discoverer = Discoverer(log=self.out)
        return self._discoverer

    def greeting(self) -> str:
        return GREETING

    # ---------- one turn ----------
    def handle(self, text: str) -> str:
        u = understand(self.extractor, text, self.pending)
        if len(text.split()) >= 6 or not self.history:
            self.lang = u.language                      # short answers ("2", "ho", "nahi") keep the language
        if u.intent == "restart":
            self.reset()
            return self.greeting()
        new_facts = {k: v for k, v in u.facts.items() if self.profile.get(k) != v}
        self.profile.update(new_facts)
        self.pending = None                             # asked once; "don't know" is not asked again
        self.history.append({"role": "user", "content": text})

        if self.stage == "chat":
            reply = self._chat_turn(text, u, new_facts)
        else:
            reply = self._advance(new_facts)
        self.history.append({"role": "assistant", "content": reply})
        return reply

    def _advance(self, new_facts: dict) -> str:
        if self.stage == "profiling":
            q = self._next_basic()
            if q:
                return self._ask(q, new_facts)
            self._discover()
            self.stage = "refining"
        if self.stage == "refining" and self.refine_asked < MAX_REFINE:
            q = next_best_question(self.profile, self.catalog, tuple(
                q for q in self.questions.values() if not q.get("synthetic")))
            if q:
                self.refine_asked += 1
                return self._ask(q, new_facts)
        self.stage = "chat"
        return self._present()

    def _next_basic(self) -> dict | None:
        for qid in BASIC_QUESTIONS:
            q = self.questions[qid]
            if qid in self.profile["_asked"] or all(self.profile.get(f) is not None for f in q["fields"]):
                continue
            return q
        return None

    def _ask(self, q: dict, new_facts: dict) -> str:
        self.pending = q
        self.profile["_asked"].append(q["id"])
        shown = dict(q, text_en=q.get(f"text_{self.lang}") or q["text_en"])
        return explain.phrase_question(self.agent, self.lang, shown, ack=new_facts or None)

    # ---------- discovery ----------
    def _discover(self) -> None:
        if not self.web:
            return
        self.out(SEARCHING.get(self.lang, SEARCHING["en"]))
        try:
            self.discovery_report = self.discoverer.discover_for_profile(
                self.profile, max_searches=self.max_searches, max_pages=self.max_pages)
        except Exception as e:  # web trouble must not end the conversation
            self.out(f"(web search unavailable, using saved schemes only: {e})")
            return
        self.catalog = self.catalog_loader()
        r = self.discovery_report
        self.out(f"(found {len(r.new_schemes)} new scheme(s) on official sites; "
                 f"{len(self.catalog)} schemes in the catalog)")

    # ---------- results and follow-ups ----------
    def _present(self) -> str:
        self.shortlist = build_shortlist(self.profile, self.catalog)
        return explain.explain_shortlist(self.agent, self.lang, self.shortlist)

    def _chat_turn(self, text: str, u, new_facts: dict) -> str:
        if new_facts:
            return self._present()
        context = {"profile": {k: v for k, v in self.profile.items() if not k.startswith("_")},
                   "shortlist": self.shortlist}
        if u.intent == "ask_about_scheme" and u.scheme_name:
            sid = find_scheme(u.scheme_name, self.catalog)
            if sid is None and self.web:
                self.out(f"(looking up {u.scheme_name} on official sites...)")
                d = self.discoverer
                try:
                    d.ingest_queries(d.name_queries([u.scheme_name]), max_searches=2, max_pages=3, per_query=1)
                    self.catalog = self.catalog_loader()
                    sid = find_scheme(u.scheme_name, self.catalog)
                except Exception as e:
                    self.out(f"(lookup failed: {e})")
            if sid:
                context["scheme_details"] = scheme_details(sid, self.profile, self.catalog)
            else:
                context["scheme_details"] = f"No official page found for {u.scheme_name!r}."
        return explain.answer(self.agent, self.lang, text, context, self.history[:-1])

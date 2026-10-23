"""The investigator: reason about a person's situation, then dig through official sites for schemes,
especially the lesser-known ones (district, department, welfare board, corporation level).

Loop (bounded by Budget):
  1. analyse  (planner LLM)  profile -> needs, each with tags and search terms   [never eligibility]
  2. plan     (code)         needs x source map -> crawl district/agency sites, targeted searches
  3. execute  (code)         SiteCrawler / Discoverer: fetch, extract with quote check, save
  4. evaluate (code)         core engine over the whole catalog
  5. reflect  (planner LLM)  what is still uncovered, which leads to chase, stop or continue
Every step emits an event so a UI can show the investigation live.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable

from core.awareness import awareness
from core.eligibility import evaluate_scheme
from discovery.catalog import load_catalog
from ingest.crawl import SiteCrawler
from ingest.fetch import load_policy
from ingest.source_map import load_sources
from llm import LLMClient, LLMError

NEED_TAGS = ["sc", "st", "obc", "minority", "women", "girl", "widow", "maternity", "senior", "disability",
             "farmer", "fisher", "construction", "livelihood", "education", "scholarship", "health",
             "housing", "youth"]

ANALYSE_SYSTEM = """You are an experienced social worker in rural India who knows how government help really
reaches people. Given one person's profile, think about ALL their needs, including non-obvious ones that
lesser-known schemes cover: children's education and hostels, a family member's disability, house repair,
livestock or tools, self-employment loans and subsidies, skill training, travel concessions, funeral or
marriage assistance, legal aid, and help for their caste/tribe/minority group, gender or occupation.
Prefer the level where little-known schemes live: district (Zilla Parishad), state departments,
welfare boards and development corporations, not only famous central schemes.
For each need give: need (short), why (one sentence tied to the profile), tags (from the allowed list),
search_terms (1-3 short web search phrases in English or Marathi/Hindi, without site names).
Also give summary: 2 sentences describing the person's situation. Never decide eligibility."""

ANALYSE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "needs": {"type": "array", "minItems": 1, "maxItems": 8, "items": {
            "type": "object",
            "properties": {
                "need": {"type": "string"},
                "why": {"type": "string"},
                "tags": {"type": "array", "items": {"enum": NEED_TAGS}},
                "search_terms": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
            },
            "required": ["need", "why", "tags", "search_terms"],
        }},
    },
    "required": ["summary", "needs"],
}

REFLECT_SYSTEM = """You review a scheme search in progress. You get the person's needs and the schemes found so
far (with their status for this person and how well-known they are). Decide what is still missing:
needs with no scheme yet, scheme names mentioned on pages that are worth looking up (leads), and new
search phrases. Favour lesser-known schemes that would bring real money or services. Set stop=true
only if every important need has at least one candidate. Never decide eligibility."""

REFLECT_SCHEMA = {
    "type": "object",
    "properties": {
        "note": {"type": "string"},
        "uncovered_needs": {"type": "array", "items": {"type": "string"}},
        "leads": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "new_search_terms": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "stop": {"type": "boolean"},
    },
    "required": ["note", "uncovered_needs", "leads", "new_search_terms", "stop"],
}


@dataclass
class Budget:
    rounds: int = 3
    searches: int = 12          # paid search calls (cached searches are free)
    pages: int = 25             # pages fetched across crawls and searches
    seconds: float = 180.0


@dataclass
class Investigation:
    analysis: dict = field(default_factory=dict)
    found: list[str] = field(default_factory=list)
    searches: int = 0
    pages: int = 0
    rounds: int = 0
    stopped_because: str = ""


def fallback_analysis(profile: dict) -> dict:
    """Used when the planner LLM is unavailable: needs read straight from the profile."""
    tags = []
    if profile.get("social_category") in ("sc", "st", "obc"):
        tags.append(profile["social_category"])
    if profile.get("gender") == "female":
        tags.append("women")
    if profile.get("marital_status") == "widowed":
        tags.append("widow")
    if (profile.get("age") or 0) >= 60:
        tags.append("senior")
    if profile.get("disability_percent"):
        tags.append("disability")
    occ = profile.get("occupation") or ""
    tags += {"farmer": ["farmer"], "agricultural_labourer": ["livelihood"], "construction_worker": ["construction"],
             "fisher": ["fisher"], "student": ["scholarship", "education"]}.get(occ, ["livelihood"])
    if profile.get("has_girl_child_under_10"):
        tags.append("girl")
    return {"summary": "Needs taken directly from the form.",
            "needs": [{"need": t, "why": "from the form", "tags": [t], "search_terms": [f"{t} scheme"]}
                      for t in dict.fromkeys(tags)]}


class Investigator:
    def __init__(self, planner: LLMClient | None, discoverer=None, budget: Budget | None = None,
                 emit: Callable[[str, dict], None] = lambda kind, data: None,
                 catalog_loader: Callable[[], dict] = load_catalog, source_map: str = "mh"):
        self.planner = planner
        self.d = discoverer
        self.budget = budget or Budget()
        self.emit = emit
        self.catalog_loader = catalog_loader
        self.source_map = source_map

    # ---------- LLM steps ----------
    def analyse(self, profile: dict) -> dict:
        facts = {k: v for k, v in profile.items() if not k.startswith("_") and v is not None}
        if self.planner is None:
            return fallback_analysis(profile)
        try:
            r = self.planner.complete([{"role": "user", "content": "Profile: " + json.dumps(facts)}],
                                      system=ANALYSE_SYSTEM, json_schema=ANALYSE_SCHEMA, max_tokens=1500)
            return r.data
        except LLMError as e:
            self.emit("notice", {"text": f"Planner unavailable, using the form only ({str(e)[:80]})"})
            return fallback_analysis(profile)

    def reflect(self, profile: dict, analysis: dict, found: list[dict], mentioned: list[str]) -> dict | None:
        if self.planner is None:
            return None
        payload = {"needs": [n["need"] for n in analysis["needs"]], "schemes_found": found[:25],
                   "names_mentioned_on_pages": list(dict.fromkeys(mentioned))[:20]}
        try:
            return self.planner.complete([{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                                         system=REFLECT_SYSTEM, json_schema=REFLECT_SCHEMA, max_tokens=1000).data
        except LLMError:
            return None

    # ---------- planning (code) ----------
    def plan(self, profile: dict, analysis: dict, round_no: int, crawled: set[str],
             terms: list[str], leads: list[str]) -> list[dict]:
        tags = {t for n in analysis["needs"] for t in n.get("tags", [])}
        sources = list(load_sources(self.source_map)) if profile.get("state") == "maharashtra" else []
        local = [s for s in sources if s.get("district") and s["district"] == profile.get("district")]
        agencies = [s for s in sources if not s.get("district") and tags & set(s.get("needs") or [])]
        actions: list[dict] = []
        for s in (local[:2] if round_no == 0 else local[2:3]) + agencies[round_no * 3: round_no * 3 + 3]:
            if s["host"] not in crawled:
                actions.append({"kind": "crawl", "source": s, "target": s["host"],
                                "why": f"{s['name']} publishes schemes for {', '.join(s.get('needs') or ['this district'])}"})
        suffix = load_policy()["search_suffix"]
        where = (profile.get("state") or "india").replace("_", " ")
        for term in terms[:3]:
            actions.append({"kind": "search", "query": f"{term} {where} eligibility {suffix}", "target": term,
                            "why": "search official sites for this need"})
        if round_no == 0 and local:
            host = local[0]["host"]
            actions.append({"kind": "search", "query": f"site:{host} योजना OR scheme", "target": host,
                            "why": "find scheme pages on the district site that its menus do not link"})
        if leads and self.d is not None:
            for q, name in zip(self.d.name_queries(leads), leads):
                actions.append({"kind": "search", "query": q, "target": name, "why": "lead found on a page"})
        return actions

    # ---------- main loop ----------
    def run(self, profile: dict) -> Investigation:
        inv = Investigation()
        t0 = time.monotonic()
        inv.analysis = self.analyse(profile)
        self.emit("thinking", {"summary": inv.analysis.get("summary", ""),
                               "needs": [{"need": n["need"], "why": n["why"]} for n in inv.analysis["needs"]]})
        if self.d is None:
            inv.stopped_because = "web search is off"
            return inv
        crawler = SiteCrawler(self.d)
        crawled: set[str] = set()
        terms = [t for n in inv.analysis["needs"] for t in n.get("search_terms", [])[:1]]
        leads: list[str] = []
        mentioned: list[str] = []
        credits0 = self.d.search.live_calls
        for round_no in range(self.budget.rounds):
            inv.rounds = round_no + 1
            actions = self.plan(profile, inv.analysis, round_no, crawled, terms, leads)
            if not actions:
                inv.stopped_because = "nothing left to try"
                break
            for act in actions:
                if time.monotonic() - t0 > self.budget.seconds:
                    inv.stopped_because = "time budget used"
                    break
                if inv.pages >= self.budget.pages:
                    inv.stopped_because = "page budget used"
                    break
                if act["kind"] == "search" and self.d.search.live_calls - credits0 >= self.budget.searches:
                    continue
                self.emit("action", {"kind": act["kind"], "target": act["target"], "why": act["why"]})
                before = set(self.catalog_loader())
                if act["kind"] == "crawl":
                    crawled.add(act["source"]["host"])
                    rep = crawler.crawl(act["source"], max_pages=min(8, self.budget.pages - inv.pages))
                    inv.pages += rep.pages_fetched
                else:
                    from discovery.discover import DiscoveryReport
                    rep = DiscoveryReport()
                    self.d.ingest_queries([act["query"]], max_searches=1,
                                          max_pages=min(3, self.budget.pages - inv.pages), per_query=2, report=rep)
                    inv.pages += rep.pages_fetched
                    mentioned += rep.mentioned
                catalog = self.catalog_loader()
                for sid in [s for s in catalog if s not in before]:
                    inv.found.append(sid)
                    s = catalog[sid]
                    self.emit("found", {"id": sid, "name": s["name"], "site": s.get("source_site", ""),
                                        "awareness": awareness(s),
                                        "status": evaluate_scheme(s, profile).status})
            inv.searches = self.d.search.live_calls - credits0
            if inv.stopped_because or round_no + 1 >= self.budget.rounds:
                break
            catalog = self.catalog_loader()
            relevant = []
            for s in catalog.values():
                st = evaluate_scheme(s, profile).status
                if st in ("eligible", "eligible_check_manually", "near_miss", "needs_info"):
                    relevant.append({"name": s["name"], "status": st, "awareness": awareness(s)})
            refl = self.reflect(profile, inv.analysis, relevant, mentioned)
            if refl is None:
                inv.stopped_because = "planner unavailable for reflection"
                break
            self.emit("thinking", {"summary": refl["note"], "uncovered": refl["uncovered_needs"]})
            if refl["stop"]:
                inv.stopped_because = "all important needs have candidates"
                break
            terms, leads = refl["new_search_terms"], refl["leads"]
        inv.searches = self.d.search.live_calls - credits0
        inv.stopped_because = inv.stopped_because or "round limit reached"
        self.emit("progress", {"text": f"Investigation finished: {len(inv.found)} new scheme(s), "
                                       f"{inv.pages} pages, {inv.searches} searches ({inv.stopped_because})"})
        return inv

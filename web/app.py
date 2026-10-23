"""Scheme Saathi web app: FastAPI API + one static page.

    python -m web            # http://localhost:8000

Endpoints
  GET  /                            the page
  GET  /api/form                    form fields (from fields.yaml) + Maharashtra districts
  POST /api/sessions                {profile, lang, web} -> {id}; starts the investigation
  GET  /api/sessions/{id}/events    Server-Sent Events (?after=N to resume)
  POST /api/sessions/{id}/answer    {field, value} | {text} | {skip: true}
  POST /api/sessions/{id}/ask       {text}  free question about the results
  GET  /api/sessions/{id}           snapshot
  GET  /api/schemes/{scheme_id}     rules with source quotes (transparency)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.awareness import awareness
from core.fields import load_fields, validate_profile
from discovery.catalog import load_catalog
from ingest.source_map import seed
from llm import LLMError, get_llm

from .session import WebSession

STATIC = Path(__file__).resolve().parent / "static"
FORM_FIELDS = ["state", "district", "age", "gender", "marital_status", "social_category", "occupation",
               "annual_family_income_inr", "ration_card_type", "disability_percent", "num_children",
               "has_girl_child_under_10", "is_student", "owns_cultivable_land", "is_minority"]


class NewSession(BaseModel):
    profile: dict[str, Any]
    lang: str = "en"
    web: bool = True


class Answer(BaseModel):
    field: str | None = None
    value: Any = None
    text: str | None = None
    skip: bool = False


class Ask(BaseModel):
    text: str


def _try_llm(role: str):
    try:
        return get_llm(role)
    except LLMError:
        return None


def create_app(planner=None, extractor=None, agent=None, discoverer_factory: Callable | None = None,
               catalog_loader: Callable[[], dict] = load_catalog, budget=None) -> FastAPI:
    app = FastAPI(title="Scheme Saathi")
    sessions: dict[str, WebSession] = {}
    llms: dict[str, Any] = {"planner": planner, "extractor": extractor, "agent": agent}

    def llm(role: str):
        if llms[role] is None:
            llms[role] = _try_llm(role)
        return llms[role]

    def discoverer():
        if discoverer_factory is not None:
            return discoverer_factory()
        try:
            from discovery.discover import Discoverer
            return Discoverer(llm=llm("extractor"))
        except Exception:
            return None                                    # no search key: catalog only

    def get_session(sid: str) -> WebSession:
        s = sessions.get(sid)
        if s is None:
            raise HTTPException(404, "session not found")
        return s

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/form")
    def form():
        fields = load_fields()
        mh = seed("mh")
        return {
            "fields": [{"name": n, "type": fields[n]["type"], "values": fields[n].get("values", []),
                        "min": fields[n].get("min"), "max": fields[n].get("max"),
                        "description": fields[n]["description"]} for n in FORM_FIELDS],
            "districts": {"maharashtra": sorted(mh["districts"])},
        }

    @app.post("/api/sessions")
    def new_session(body: NewSession):
        raw = {k: v for k, v in body.profile.items() if k in FORM_FIELDS and v not in (None, "")}
        profile, errors = validate_profile(raw)
        if errors:
            raise HTTPException(422, {"errors": errors})
        s = WebSession(profile, body.lang, planner=llm("planner"), extractor=llm("extractor"), agent=llm("agent"),
                       discoverer=discoverer() if body.web else None, catalog_loader=catalog_loader, budget=budget)
        sessions[s.id] = s
        s.start()
        return {"id": s.id, "profile": s.public_profile()}

    @app.get("/api/sessions/{sid}/events")
    def events(sid: str, after: int = 0, once: bool = False):
        s = get_session(sid)
        if once:                                             # plain JSON, for tests and debugging
            return s.events[after:]

        def stream():
            n = after
            while True:                                      # stays open: replies can come after "done"
                batch = s.wait_events(n, timeout=15)
                if not batch:
                    yield ": keep-alive\n\n"
                    continue
                for ev in batch:
                    yield f"id: {ev['seq']}\ndata: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
                    n = ev["seq"] + 1
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/sessions/{sid}/answer")
    def answer(sid: str, body: Answer):
        out = get_session(sid).answer(body.field, body.value, body.text, body.skip)
        if "error" in out:
            raise HTTPException(422, out["error"])
        return out

    @app.post("/api/sessions/{sid}/ask")
    def ask(sid: str, body: Ask):
        get_session(sid).ask(body.text)
        return {"ok": True}

    @app.get("/api/sessions/{sid}")
    def snapshot(sid: str):
        return get_session(sid).snapshot()

    @app.get("/api/schemes/{scheme_id}")
    def scheme(scheme_id: str):
        s = catalog_loader().get(scheme_id)
        if s is None:
            raise HTTPException(404, "scheme not found")
        rules = (s["eligibility"].get("all") or []) + (s["eligibility"].get("any") or [])
        return {"id": scheme_id, "name": s["name"], "source_url": s["source_url"],
                "source_site": s.get("source_site", ""), "verified": s.get("verified", s.get("origin") != "discovered"),
                "awareness": awareness(s), "agency": s.get("agency_name") or s.get("ministry") or "",
                "benefit": s.get("benefit_summary", ""), "how_to_apply": s.get("how_to_apply", ""),
                "rules": [{"rule": r.get("reason", ""), "quote": r.get("source_quote", "")} for r in rules],
                "check_yourself": s.get("unmapped_criteria", []),
                "documents": [d["name"] for d in s.get("documents") or []]}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()

# Scheme Saathi: backend build plan (v0.4 onward)

## Context

Phase 1 (v0.2) built a deterministic core in `scheme_saathi/core/` (eligibility, ranking, questions, name matching, preflight, status decoder, `pipeline.assist()`), with 3 hand-written schemes and 83 passing tests. No WhatsApp, WAHA, voice or website is wanted.

The goal is a **strong backend** that:
1. holds a conversation: asks the user a few questions, takes their answers,
2. knows **as many Indian schemes as possible**, found and scraped from the web (central + all states), not only the 3 curated ones,
3. decides eligibility deterministically and recommends the **one most beneficial scheme** first, with a queue, preflight checklist and status decoding.

The rules in CLAUDE.md section 0 still hold: the LLM talks and extracts, code decides; the LLM is swappable per role (Anthropic, OpenAI-compatible and Gemini keys are all available); tests never touch the network; no Aadhaar; every answer carries a source URL and date; web-found rules stay `verified: false`.

**One change from CLAUDE.md:** web discovery is split into two parts. "Search per query" alone cannot reach "as many schemes as possible", and it would be slow and expensive on every user turn. So:
- **Bulk ingestion (offline, scheduled):** crawl official scheme directories and build a large local catalog.
- **Live search (online, per user):** fill gaps for the user's state or occupation when the catalog has few matches.

---

## Target architecture

```
scheme_saathi/
  llm/                    swappable provider layer (Phase A)
  schemes/
    fields.yaml           canonical field vocabulary (grows to ~40 fields)
    questions.yaml        generated + hand-tuned questions per field (en/hi/mr)
    curated/*.yaml        hand-verified schemes (move existing 3 here)
    discovered/*.yaml     LLM-extracted, verified:false
  ingest/                 bulk crawl pipeline (Phase C)
    sources.yaml          list of directories to crawl + robots/ToS notes
    crawl.py  fetch.py  parse.py  extract.py  validate.py  run.py
  discovery/              live per-profile search (Phase D)
    search.py  discover.py  catalog.py
  core/                   existing deterministic engine (extended)
  conversation/           session state machine (Phase E)
    session.py  turn.py  understand.py  explain.py
  api/                    FastAPI service (Phase F)
    main.py  schemas.py  store.py (SQLite via SQLModel; Postgres-ready URL)
  saathi/__main__.py      CLI chat: python -m saathi
  saathi/eval/            evaluator (Phase G)
```

---

## Phases (one per session where possible; log each in PROGRESS.md)

### Phase A: LLM layer (CLAUDE.md task 1)
- `llm/base.py`: `LLMClient` protocol, `LLMResponse` exactly as in CLAUDE.md section 5.
- `llm/providers/`: `anthropic_provider.py`, `openai_provider.py` (with `base_url` for Groq/Ollama/etc.), `gemini_provider.py`, `mock_provider.py` (canned JSON by prompt key).
- `llm/config.py`: `get_llm(role)` for `extractor` / `agent` / `judge`, with `LLM_PROVIDER` fallback; warns when judge == extractor.
- JSON validated with pydantic; one retry with the error, then a clear failure. Log each call to `logs/llm_calls.jsonl` with PII redacted.
- Add `python-dotenv`, `.env.example`.
- **Tests:** mock provider, config resolution, retry-on-invalid-JSON, a test that greps that no module outside `llm/providers/` imports a vendor SDK.

### Phase B: Field vocabulary that can cover many schemes (task 2, enlarged)
- `schemes/fields.yaml`: name, type (bool/int/enum/date/str), allowed values, description, question id, `group`. Start with today's 12 fields plus the fields most Indian schemes use: `state`, `district`, `residence` (rural/urban), `gender`, `age`, `marital_status` (incl. widow), `social_category` (SC/ST/OBC/EWS/General/Minority), `annual_family_income_inr`, `is_bpl`, `ration_card_type`, `occupation` (farmer/labourer/street vendor/artisan/fisher/student/unemployed/...), `is_student`, `education_level`, `disability_percent`, `land_acres`, `has_bank_account`, `is_pregnant_or_lactating`, `num_children`, `girl_child`, `is_construction_worker_registered`, `is_minority`, `is_ex_serviceman`.
- `core/loader.py`: reject unknown fields and wrong value types; support `unmapped_criteria`.
- **Tests:** loader rejects unknown fields; existing 3 schemes still load.

### Phase C: Bulk scheme ingestion (the "as many as possible" part)
1. **Source survey first (no code, ~1 short session).** Write `ingest/sources.yaml` listing each candidate source with its robots.txt result, terms-of-use note and access method. Candidates: myscheme.gov.in (largest directory, ~3,000+ schemes; check its robots/ToS and whether it offers a public API or sitemap), india.gov.in, data.gov.in datasets, state portals (e.g. MahaDBT for Maharashtra), ministry pages on `*.gov.in` / `*.nic.in`. Only sources that allow crawling go in. Never bypass a block, login or captcha.
2. `ingest/fetch.py`: httpx with on-disk cache (`cache/web/`), robots.txt check, per-domain rate limit (e.g. 1 req/2 s), user agent naming the project, PDF via pdfplumber, HTML via trafilatura, `content_hash`.
3. `ingest/crawl.py`: per-source adapter that lists scheme detail URLs (sitemap or listing pages). Resumable: state in `cache/crawl_state.json`.
4. `ingest/extract.py` (role `extractor`): page text → scheme dict in the existing YAML schema, using only `fields.yaml` fields; anything else → `unmapped_criteria`. Each rule carries `source_quote`; extract `benefit_annual_inr` and `rank.value_score/effort` hints; add `origin, verified:false, extracted_at, extractor_model, source_url, fetched_at, content_hash, state, ministry`.
5. `ingest/validate.py`: `loader._validate` + quote must appear in fetched text (normalised whitespace) else drop the rule + type checks. Schemes with zero valid rules are kept as "info only" (shown, never auto-eligible).
6. `ingest/run.py`: `python -m ingest --source myscheme --limit 50` → writes `schemes/discovered/<id>.yaml`, skips unchanged `content_hash`. Report counts (fetched / extracted / rules dropped / cost).
7. `discovery/catalog.py`: merge curated + discovered, dedupe by normalised name/aliases (reuse `core/name_match` normaliser), curated wins.
- **Scale concerns:** cost control (batch, cache, cheap extractor model, re-extract only on hash change); catalog index file `schemes/index.json` (id, state, fields used, benefit) so loading thousands of YAMLs stays fast.
- **Tests:** saved HTML/PDF fixtures under `tests/fixtures/web/`, mock extractor; quote check drops a fake rule; robots disallow is respected; dedupe.

### Phase D: Live per-profile search (CLAUDE.md section 6)
- `discovery/search.py`: `SearchProvider` protocol; Tavily / Brave / SerpAPI / Google CSE by `SEARCH_PROVIDER`.
- `discovery/discover.py`: `discover_schemes(profile)` — builds queries from state/occupation/category, prefers official domains, fetches and extracts through the same `ingest/fetch.py` + `extract.py` + `validate.py`, caches into `schemes/discovered/`. Runs only when the catalog has fewer than N candidate schemes for the profile, or on explicit request; has a time budget.
- **Tests:** fake search provider with fixture results.

### Phase E: Engine changes + conversation (the "asks questions, user answers" part)
- **Engine (task 6):**
  - `core/eligibility.py`: report `origin`, `verified`; `eligible_check_manually` when `unmapped_criteria` exist; prefilter by `state` (central + user's state only).
  - `core/ranking.py`: "most beneficial" = benefit value (use `benefit_annual_inr` normalised, then `value_score`) minus effort; verified beats unverified on ties; keep anti-overload (one `start_with` + queue).
  - `core/questions.py`: with hundreds of schemes, choose the question with the highest information gain = number of still-undecided candidate schemes (weighted by benefit) that the field would decide. Always ask `state` and `age` early. Hard cap 7 questions, then decide with what's known. Questions come from `fields.yaml` → `questions.yaml` (hi/mr/en text generated once by LLM, reviewed, committed).
  - Fix known issue: follow-up question when the combined exclusions question gets "yes".
- **Conversation (`conversation/`):**
  - `session.py`: `Session` (id, language, profile, asked questions, candidate schemes, chosen scheme, records, stage). Stages: `greeting/consent → profiling → recommending → preflight → status_help → done`.
  - `understand.py` (role `extractor`): user's free text (hi/mr/en, may answer several things at once) → field updates validated against `fields.yaml`; read back numbers/names.
  - `turn.py`: `handle_turn(session, user_text) -> reply`: understand → `apply` → if more info needed `next_best_question` → else `check_eligibility` + `rank_schemes` (+ `discover_schemes` if catalog thin) → preflight/checklist on request → `explain`.
  - `explain.py` (role `agent`): writes a short reply in the user's language **only from the JSON result**; says "found on <site>, not yet verified" for discovered schemes; never states eligibility not in the JSON.
  - Hard cap 6 LLM/tool iterations per turn.
- **CLI:** `python -m saathi` (interactive chat) and `python -m saathi "query" --state Maharashtra --lang hi` (one shot).
- **Tests:** scripted multi-turn conversations with the mock LLM; question count ≤ 7; a test that fails if the reply claims a scheme not in the engine output.

### Phase F: HTTP backend (so any front end can plug in later)
- `api/main.py` (FastAPI):
  - `POST /sessions` → session id + first question
  - `POST /sessions/{id}/messages` {text} → reply, next question, current recommendation JSON
  - `GET /sessions/{id}` → profile, results, start_with, queue
  - `POST /sessions/{id}/preflight` {records} and `POST /sessions/{id}/status` {status_text}
  - `POST /sessions/{id}/confirm` → summary + SHA-256 payload hash; `POST /sessions/{id}/submit` {confirmation_id} only succeeds if hash matches (blueprint 3.2; produces JSON/PDF packet, no real portal)
  - `GET /schemes?state=&q=` and `GET /schemes/{id}` (transparency: rules, source, last verified)
  - `DELETE /sessions/{id}` (delete my data)
  - `GET /health`
- `api/store.py`: SQLModel on SQLite by default (`DATABASE_URL` can point to Postgres later); tables: sessions, messages (redacted), audit_log (every tool call), confirmations.
- API key header auth, CORS from env, per-session lock.
- **Tests:** FastAPI `TestClient` end-to-end with mock LLM; confirmation tamper test (edit payload → submit rejected).

### Phase G: Evaluator (task 8) + test data growth (task 9)
- `python -m saathi.eval [--judge-provider X --judge-model Y]` → `eval_results/<ts>_<judge>.json`.
- Deterministic: eligibility accuracy (30 personas), mean questions (5–7), name matcher P/R (100+ pairs), status top-3 hit rate.
- Judge: extraction faithfulness (quote supports rule), profile extraction vs 30+ gold hi/mr/en queries, discovery recall vs hand-made expected lists, reply quality rubric.
- Ingestion quality report: % rules kept after quote check, % schemes with ≥1 mapped rule, coverage by state.

---

## Order and milestones

| # | Phase | Milestone you can see |
|---|---|---|
| 1 | A LLM layer | `get_llm("extractor").complete(...)` works with all 3 providers + mock |
| 2 | B fields.yaml | loader rejects unknown fields; vocabulary ~40 fields |
| 3 | C1 source survey | `ingest/sources.yaml` with robots/ToS verdicts |
| 4 | C2–C7 ingestion | `python -m ingest --source X --limit 50` → 50 YAMLs, quality report |
| 5 | E engine + conversation + CLI | `python -m saathi` chat recommends one scheme from curated + discovered |
| 6 | D live search | thin-catalog profiles trigger search and get new schemes |
| 7 | F FastAPI | same chat over HTTP; confirmation gate |
| 8 | C scale-up | run ingestion across all allowed sources (hundreds → thousands) |
| 9 | G evaluator + data | metrics file + numbers in PROGRESS.md |

**When the web scraping happens:** steps 3, 4 and 8 are the scraping work (Phase C), and step 6 adds live search for each user.
- Step 3 (session 3): check each source's robots.txt and terms before scraping it.
- Step 4 (session 4): build the scraper, fetch pages, have the LLM extract the rules, check the quotes, and save YAML for the first 50 schemes.
- Step 8: run the same scraper over every allowed source to get hundreds or thousands of schemes.
- Step 6: when the saved catalog has few schemes for a user's profile, the backend searches the web for that user.

Steps 1 and 2 must come first. The scraper needs the LLM layer to extract rules, and it needs `fields.yaml` so every scraped scheme maps to the same fields.

Ingestion (C) comes before live search (D) because it gives the most schemes per unit of effort and the conversation needs a big catalog to be useful.

## Critical files to modify / reuse
- Reuse: `core/eligibility.py`, `core/ranking.py`, `core/questions.py`, `core/loader.py` (`_validate`), `core/name_match.py` (normaliser for dedupe), `core/preflight.py`, `core/status_decoder.py`, `core/checklist.py`, `core/pipeline.py` (`assist()` becomes what `turn.py` calls).
- Update every session: `PROGRESS.md` (entry + current-state block); update `CLAUDE.md` sections 3, 6 and 8 to record the ingestion/live-search split and the API phase.
- Update `requirements.txt`: python-dotenv, pydantic, httpx, trafilatura, pdfplumber, fastapi, uvicorn, sqlmodel, plus vendor SDKs (anthropic, openai, google-genai).

## Verification
- Every session: `python -m pytest -q` (no network), count in PROGRESS.md.
- Phase C: run `python -m ingest --source <allowed source> --limit 20` live, open 3 random YAMLs and check each `source_quote` against the page by hand.
- Phase E: `python -m saathi` with a Hindi query (e.g. "मेरी उम्र 62 है, 2 एकड़ ज़मीन है, BPL कार्ड है") → ≤ 7 questions → one recommended scheme with source URL, unverified label where relevant.
- Phase F: `uvicorn api.main:app` then a curl script that runs a full session, plus the confirmation tamper check.
- Phase G: `python -m saathi.eval` with judge on a different provider than extractor.

## Risks to watch
- Scraping terms: some portals may forbid crawling; those are skipped, not bypassed.
- LLM extraction errors at scale: quote check + `verified:false` + "check manually" status + judge faithfulness score.
- Cost: cheap extractor model, cache by content hash, `--limit` on every run.
- Question explosion with many schemes: information-gain selection + 7-question cap + state prefilter.

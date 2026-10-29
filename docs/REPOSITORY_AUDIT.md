# Consolidation audit — 2026-10-28

## Backup and cleanup

A complete external sibling backup was created before cleanup, including the original `.git`, source, documents, public database and runtime logs. Only disposable Python environments/caches were excluded. The exact local backup location is recorded in the handoff report, not hardcoded into shared setup scripts.

Removed from the working project after confirming the backup and runtime references:

- `web/app.py.v22.bak` — generated installer snapshot.
- `web/conversation_api.v2-backup.py` — superseded, unimported snapshot.
- `web/static/chat.v2-backup.html` — superseded static snapshot.
- `logs/llm_calls.jsonl` — private runtime output.
- Generated Python and pytest caches.
- The broken, disposable virtual environment and temporary validation helpers; replaced with a standard Python 3.13 virtual environment.

Preserved all current source modules, tests, configuration templates, original and current interfaces, technical documents, source maps, the historical installer, and the public scheme catalog. Benchmark and model-smoke scripts are intentional developer tools and were retained. No downloaded archives existed in the source tree.

`.gitignore` now excludes private environment variants, virtual environments, caches, logs, generated test/browser output, citizen data, credentials and private runtime databases. `data/schemes.db` is an explicit exception. `.gitattributes` normalizes text line endings and preserves binary assets.

## Data and secrets

- Catalog: 267 public schemes, 1,008 crawl URLs, empty metadata table; SQLite integrity check passed.
- Scheme rows match the existing remote catalog. No citizen/account/session table or identity-field key was found.
- The root environment template contains no credential values. Optional WAHA settings are disabled/empty by default.
- `detect-secrets` reviewed staged source and 176 historical text-file versions across both remote commits. No real credential was identified; two new-source hits are fictional WAHA test fixtures.
- Runtime logs, environments and generated scan reports are not staged. See `SECURITY.md` for scan commands and limitations.

## Git integration strategy

The local ZIP-derived checkout had no commits or tracked files. The remote had only the default `refactor` branch at `054a768ee47f2b5461947ac442dbf66b726d2669`; there was no `main`.

A separate temporary clone of `refactor` preserves both remote commits and their authors. Normalized comparison found no remote-only files and only the expected website/routing differences in common source. The reviewed local working source was transferred without deleting remote files. A new `main` is based on the existing `refactor` tip, with one consolidated commit. Publication uses an ordinary push; no force-push, hard reset, destructive clean, branch deletion or remote default-branch change is part of this procedure. A final remote-ref check prevents overwriting newly arriving work.

## Reproducibility and validation

A signed official Python 3.13 installation created fresh environments independently of any private agent runtime. Runtime dependencies are recorded in `requirements.lock.txt`; development-only browser/scanning tools are separate. Windows launchers explicitly select the project environment. Setup instructions and browser scripts no longer depend on developer-specific Windows paths.

- Full offline suite: **237 passed, 0 failed** in the fresh integration environment.
- `pip check`: no broken requirements.
- Actual FastAPI routes and static assets checked over HTTP; `/chat` redirects to `/app` and the public/dashboard/legacy bodies are distinct.
- Chromium at 1440, 1024, 768, 390 and 320 pixels: landing translations, navigation, app flows, scheme rendering, real Rescue responses, channels, legacy and Swagger passed without horizontal overflow or unexpected console errors.
- Hosted-model extraction and external WhatsApp delivery were not exercised. LiveKit is still coming soon.

The publication handoff records the final commit SHA and remote result. Test/browser artifacts remain ignored local output.

## Project structure

```text
.github/workflows/     Offline CI
core/                 Deterministic eligibility, ranking and Rescue logic
data/schemes.db       Reviewed public catalog
discovery/            Catalog and official-source discovery
docs/                 Architecture, audit, design and integration notes
ingest/               Source maps, fetching, extraction and crawling
llm/                  Provider abstraction and adapters
saathi/               Conversation and investigation orchestration
schemes/              Field/question vocabulary and flagship configuration
scripts/              Intentional developer and browser-check tools
store/                Public catalog persistence and migration
tests/                Offline regression suite and fictional fixtures
web/                  FastAPI APIs and public/app/legacy frontend assets
```

## Remaining deployment work

Configure a model service for live conversational extraction. Validate opted-in WAHA delivery, then add deployment authentication/rate limits, durable storage and webhook processing as needed. Review unverified scheme rules against current official sources. Voice calls, government submissions and verified government-status lookups are not implemented.

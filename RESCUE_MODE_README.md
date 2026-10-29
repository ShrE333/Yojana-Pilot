# Yojana-Pilot: Rescue Mode phase 1

This is an additive patch against the repository's `refactor` branch as inspected on 2026-10-25.

## Installation

Copy `web/rescue_api.py` to the repo's `web/` folder and `tests/test_rescue_api.py` to `tests/`.

In existing `web/app.py`, add near the other imports:

```python
from .rescue_api import router as rescue_router
```

Inside `create_app`, add immediately after `app = FastAPI(title="Scheme Saathi")`:

```python
    app.include_router(rescue_router)
```

These are the only changes required to existing files. Nothing here modifies the database or existing chat flow.

## Run

```powershell
python -m pip install -r requirements.txt
python -m pytest -q tests/test_rescue_api.py
python -m pytest -q
python -m web
```

Local documentation: `http://localhost:8000/docs`.

Example request:

```powershell
$body = @{
  scheme_id = 'pm_kisan'
  records = @{
    aadhaar = @{name='Sunil Patil'}
    land_record = @{name='Suneel Patil'}
  }
  facts = @{ekyc_done=$false}
  status_text = 'eKYC pending'
} | ConvertTo-Json -Depth 6
Invoke-RestMethod -Uri http://localhost:8000/api/rescue/diagnose -Method Post -ContentType application/json -Body $body
```

## What it does

- Exposes the existing deterministic cross-document preflight and status decoder by API.
- Rejects unknown scheme IDs, unsupported input fields and unverified discovered schemes.
- Does not call real government portals, infer actual payment status or collect full Aadhaar numbers.
- All endpoints remain stateless (no personal information is persisted by this patch).

## Limitations before public deployment

- Add authentication, HTTPS and rate limiting before accepting citizen data.
- Validate accepted document types and scheme-specific data against official guidance.
- Only the existing curated `pm_kisan` sample is exercised by these tests.
- The current dashboard wires the preflight and status APIs. OCR, voice, application submission and human confirmation workflows are not implemented.

## Hosted model, CPU VM

No GPU or LLM change is necessary for Rescue Mode. The existing OpenAI-compatible provider can be configured separately with a free-tier key:

```env
LLM_PROVIDER=openai
PLANNER_PROVIDER=openai
EXTRACTOR_PROVIDER=openai
AGENT_PROVIDER=openai
OPENAI_BASE_URL=https://api.groq.com/openai/v1
OPENAI_API_KEY=YOUR_GROQ_KEY
PLANNER_MODEL=YOUR_CURRENTLY_AVAILABLE_MODEL_ID
EXTRACTOR_MODEL=YOUR_CURRENTLY_AVAILABLE_MODEL_ID
AGENT_MODEL=YOUR_CURRENTLY_AVAILABLE_MODEL_ID
```

Check your provider dashboard for current free model IDs, quotas and JSON compatibility; do not commit `.env`.

# Yojana-Pilot V2.2 — Website + WhatsApp beta

Historical installer notes: the consolidated Git checkout already includes these changes. Use README.md for current setup and `/app` for the dashboard; running the old ZIP installer is unnecessary.

This ZIP has **project-root-relative files**. Extract its *contents* into the existing `Yojana-Pilot` folder and permit replacement of `web/static/chat.html`. It does not contain a `Yojana-Pilot/` parent directory.

## New / replaced files
- REPLACE `web/static/chat.html`: responsive multi-page website (assistant, discovery, rescue, channels). No CDN dependency.
- ADD `web/waha_bridge.py`: WAHA inbound text bridge, opt-in and STOP, basic dedupe, 1-to-1 only.
- ADD `tests/test_waha_bridge.py`: focused tests.
- ADD this update document and `INSTALL_V2_2.ps1`.

**Important:** For maximum safety, `web/app.py` is NOT replaced. Use the installer to insert the WAHA router, preserving the Rescue and conversation routers already present.

## Local installation
From within the existing project root after extracting the ZIP:
```
powershell -ExecutionPolicy Bypass -File .\INSTALL_V2_2.ps1
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m web
```
Open `http://localhost:8000/chat`. Open `http://localhost:8000/api/integrations/waha/status` to inspect config status.

## WAHA configuration (server environment, NOT the browser)
```
YOJANA_WAHA_ENABLED=true
WAHA_BASE_URL=https://YOUR-WAHA-SERVER
WAHA_API_KEY=YOUR_WAHA_KEY
WAHA_SESSION=default
YOJANA_WAHA_WEBHOOK_SECRET=GENERATE_AT_LEAST_24_RANDOM_CHARACTERS
```
Configure your WAHA session to send **only `message` events** to:
`https://YOUR-YOJANA-DOMAIN/api/integrations/waha/YOUR_RANDOM_WEBHOOK_SECRET`
Test with one private account. A citizen must message START first; subsequent text replies use the same conversation endpoint as the website. STOP turns off replies. Do not use a test group.

WAHA `POST /api/sendText` uses `session`, `chatId`, and `text`, authenticated with `X-Api-Key`. Inbound webhook payloads use event `message`. See https://waha.devlike.pro/docs/how-to/send-messages/ and https://waha.devlike.pro/docs/how-to/receive-messages/.

## Not production ready
- Deduplication and chat-to-session mapping are memory-only; restart loses subscriptions. Migrate to Postgres/Redis before broad deployment.
- HTTP webhook work is synchronous; move to durable queue with retries, exponential backoff and dead letters. The receiver currently does not verify webhook source except for a secret path and the optional network perimeter.
- Never print webhook URLs containing the secret; limit access at firewall/reverse proxy. Place WAHA behind HTTPS and keep API keys server-side.
- V2 conversation engine itself sends messages to hosted LLM and lacks robust PII filtering, rate limits and persistent case history.
- Browser voice calls, inbound PSTN calls, WhatsApp media/voice notes, WhatsApp cross-device handoff, official application submission and government status verification are NOT implemented.
- WAHA is unofficial and may violate WhatsApp terms or expose accounts to restrictions; assess this before any public rollout.
- Do not use your real citizen personal data in the demo.

## Why no WAHA keys in the website?
WAHA credentials must NEVER be exposed to web visitors. Configure them privately in `.env` on the backend or in your EasyPanel secret settings.

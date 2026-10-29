# V2.2 dashboard integration

The public website now lives at /, the workspace at /app, and /chat redirects to /app. See [Website architecture and restart instructions](WEBSITE_ARCHITECTURE.md) for current details.

The installer previously registered the WAHA router but did not change the homepage handler. `web/app.py` continued to serve the legacy `web/static/index.html` at `/`, while the new workspace was installed as `web/static/chat.html` and served only at `/chat`. Existing homepage tests only checked the old product name and JavaScript availability.

## Routes and assets

- `/`: public landing page. `/app`: citizen workspace. `/chat`: 307 redirect to `/app`.
- `/legacy`: the unchanged form interface (`index.html`, `app.js`, `style.css`).
- `/static/dashboard.css` and `/static/dashboard.js`: dashboard assets.
- `/api/catalog`: catalog names, Rescue availability, and configured dependencies for scheme selection.
- Existing conversation, session/SSE, discovery, scheme transparency, Rescue, WAHA, and documentation routes remain available.

## Functional scope

The dashboard shows conversation state, validated profile fields, recommendation counts, current-session history, and recent activity. Conversation requests use `/api/v2/conversation`, with English/Hindi/Marathi language selection, eligibility-based follow-up reasons, error messages, retry, reset, and loading states. Extraction still requires the existing configured language service. The eligibility engine remains deterministic and unchanged.

Scheme results show engine statuses, benefits, document requirements, application guidance, official source links, manual-check conditions, and verification notices. Recommendations come from the current conversation and existing catalog; the legacy workflow retains live official-site discovery.

Rescue uses real `/api/rescue/preflight` and `/api/rescue/diagnose` calls. It supports configured scheme selection, optional name comparison across four document types, scheme-specific dependency inputs, a dependency map, ordered corrective actions, and user-reported portal text interpretation. No government beneficiary records are queried. PM-JAY's existing `ayushman_card_made` dependency is now accepted by the API.

WhatsApp displays server configuration and enablement without claiming a live connection. Administrator setup instructions are shown, while the existing opt-in WAHA webhook remains unchanged. Live messaging was not tested against an external WAHA instance.

LiveKit calling and voice notes are explicitly upcoming and disabled. Persistent cross-restart history, government submissions, and verified government status lookups are not implemented.

## Run

With a working project virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m web
```

Open http://localhost:8000. Restart an already running backend after changing Python routes.

Use a standard Python 3.13 virtual environment created as described in the project README. The public page is at `/`; open `/app` for the dashboard.

Optional browser checks require the development dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe scripts/check_dashboard_browser.py
```

This script checks desktop and mobile layouts and captures screenshots under `artifacts/browser/`. Its successful conversation reply substitutes model extraction with a fixture and uses the real deterministic recommendation engine. Rescue requests go to the running backend. A deliberate 503 tests the chat retry state; that expected console entry is excluded from unexpected-error assertions.

## Previous dashboard integration validation

These earlier results are superseded by the current public-site suite: 237 passed. See WEBSITE_ARCHITECTURE.md.

- Full automated suite: **232 passed, 0 failed** (227 existing tests plus five dashboard/API regression tests).
- Focused dashboard suite, including OpenAPI generation: **5 passed**.
- Chromium: **1440px, 390px, and 320px** checks passed. Verified routing, CSS/JS loading, navigation, conversation rendering with a model fixture, retry/reset, real Rescue responses, channel notices, rendered Swagger documentation, and no page overflow or unexpected console errors.
- Those earlier tests used a temporary runtime because the original environment was broken. Current reproducible setup uses a standard Python installation; see README.md.
- Live hosted model extraction and external WhatsApp delivery were not exercised. No live government status is claimed.

## Changed files

`web/app.py`, `web/conversation_api.py`, `web/rescue_api.py`, `web/static/chat.html`, `web/static/dashboard.css`, `web/static/dashboard.js`, `tests/test_dashboard.py`, `scripts/check_dashboard_browser.py`, `INSTALL_V2_2.ps1`, and this document. The legacy HTML, JavaScript, CSS and eligibility engine were preserved.

For subsequent repository cleanup and publication, see REPOSITORY_AUDIT.md.

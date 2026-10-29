# Public website and citizen workspace

## Routing contract

| Route | Response | Purpose |
| --- | --- | --- |
| `/` | 200, `landing.html` | Public website |
| `/app` | 200, `chat.html` | V2.2 citizen workspace |
| `/chat` | 307, `Location: /app` | Backward compatibility |
| `/legacy` | 200, `index.html` | Original Scheme Saathi form |
| `/docs` | 200 | Existing FastAPI Swagger documentation |
| `/static/*` | Static files | Page-specific and shared assets |

Each public path has one registration in `web/app.py`. Conversation, Rescue, WAHA, discovery/session/SSE, scheme transparency and catalog APIs are preserved. The eligibility engine is unchanged.

`landing.css` and `landing.js` belong to the public site; `dashboard.css` and `dashboard.js` belong to the workspace. `brand.css` and `favicon.svg` provide the shared identity. Original `app.js` and `style.css` remain dedicated to the legacy interface. No root static mount or client-side router intercepts public routes.

Dashboard deep links support `/app#schemes`, `/app#rescue` and `/app#channels`. Only the language preference is persisted in browser storage, never conversation text or profiles.

## What the running server actually returned

During the routing investigation, one older IPv4 validation server owned port 8000. Direct HTTP requests to localhost and 127.0.0.1 returned the dashboard, not the legacy form. No IPv6 listener was present. The intermittent legacy response was not reproduced and was not attributed to caching.

The source had no public landing handler: `/` and `/chat` shared the dashboard response. The launcher also scheduled a browser tab before Uvicorn successfully bound the port. A failed second launch could therefore open the older listener's page. Editing Python routes does not change an already running non-reloading server.

The launcher now reserves the port first, imports the app from its own package, prints the PID and resolved source path, and opens a browser only after application startup succeeds. An occupied-port launch exits with diagnostics; it does not stop the listener or open its website. This was verified with automated tests and against the real occupied port.

Only the confirmed project server was stopped during replacement. Never use a PID copied from old documentation: inspect the current listener and compare it with the launcher's PID and source path.

## Design and availability

The public page uses local SVG illustrations/icons, system fonts with Devanagari fallbacks, a coherent emerald/navy identity, responsive layouts, keyboard-accessible menus, native FAQ disclosures, a skip link and reduced-motion support. It has no external font, analytics, frontend-framework or image-service dependency. Content and navigation remain readable without JavaScript; language switching and configuration status require JavaScript.

All public copy has English, Hindi and Marathi variants. The selected language carries into the dashboard input selector. Conversation still needs the existing configured language service. Code-based eligibility decisions remain separate from AI extraction. Illustrations and the conversation preview are labelled illustrative.

WhatsApp status comes from the existing WAHA endpoint; configured/enabled does not imply verified connectivity. LiveKit calls and voice notes are explicitly coming soon. No partnership, beneficiary count, testimonial, success metric or live government connection is claimed. Privacy copy explains voluntary sharing, model processing, possible logging, temporary sessions and the absence of government submissions.

## Validation

- Full pytest suite: **237 passed, 0 failed**.
- Focused routing/launcher suite: **10 passed**.
- Real HTTP: `/`, `/app`, `/legacy`, `/docs`, `/openapi.json` and all UI assets return 200. `/chat` returns 307 with `Location: /app`. The three page bodies are distinct.
- Chromium: **1440, 1024, 768, 390 and 320 pixels**. Checked both translations, mobile menu open/close/Escape/focus, FAQ disclosures, CTAs, routes, section links, scheme rendering, chat retry/reset, real Rescue requests, channel notices and no horizontal overflow.
- Swagger was rendered in Chromium and API operations were visible. It retains FastAPI's default CDN assets.
- No unexpected console or JavaScript errors. A deliberate conversation 503 tests retry; that expected entry is excluded from the smoke assertion.
- Successful conversation rendering uses a model-extraction fixture and the real deterministic recommendation engine. Hosted-model and external WhatsApp delivery were not exercised.

Browser script: `scripts/check_dashboard_browser.py`. Screenshots: `artifacts/browser/`, including Hindi and Marathi renders. Playwright is a validation-only dependency.

## Reproducible Windows setup and restart

Use a standard Python 3.13 installation. From your checkout root:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m web --no-browser
```

Use `python -m venv .venv` if your Python installation does not provide `py`. No custom PYTHONPATH or internal agent runtime is needed. Copy `.env.example` to `.env` only if `.env` does not already exist; model credentials are needed only for live language-service calls.

Before restarting, inspect the listener:

```powershell
netstat -ano -p tcp | Select-String ':8000\s+.*LISTENING'
```

Compare the returned PID with the current launcher's PID and source path. Stop only that confirmed server with Ctrl+C in its terminal. If using `Stop-Process`, inspect `Get-Process -Id <current-pid>` first. Do not stop every Python process. Use `--port 8001` if another service owns 8000.

Open http://127.0.0.1:8000/ for the public page and http://127.0.0.1:8000/app for the workspace.

With the backend running, browser checks run in a second terminal:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe scripts/check_dashboard_browser.py
```

Set `BROWSER_EXECUTABLE` to use a browser already installed on your machine instead. Browser output is ignored by Git.

## Changed files

- `web/app.py`, `web/__main__.py`
- `web/static/landing.html`, `web/static/landing.css`, `web/static/landing.js`
- `web/static/brand.css`, `web/static/favicon.svg`
- `web/static/chat.html`, `web/static/dashboard.css`, `web/static/dashboard.js`
- `tests/test_dashboard.py`, `tests/test_web.py`, `tests/test_web_launcher.py`
- `scripts/check_dashboard_browser.py`
- `docs/DASHBOARD_V22.md`, `docs/WEBSITE_ARCHITECTURE.md`

This document describes the website integration. For subsequent cleanup and publication details, see REPOSITORY_AUDIT.md.

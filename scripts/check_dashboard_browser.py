"""Optional real-browser checks: python scripts/check_dashboard_browser.py.

Install requirements-dev.txt and run python -m playwright install chromium first.
Set BROWSER_EXECUTABLE to use an existing browser; no hosted LLM is called.
Run the app separately at http://127.0.0.1:8000 (or set DASHBOARD_URL).
"""
import json
import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from discovery.catalog import load_catalog
from saathi.shortlist import build_shortlist

BASE = os.environ.get('DASHBOARD_URL', 'http://127.0.0.1:8000')
OUT = Path(os.environ.get('DASHBOARD_ARTIFACTS', 'artifacts/browser'))
OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser_path = os.environ.get('BROWSER_EXECUTABLE')
    browser = p.chromium.launch(headless=True, **({'executable_path': browser_path} if browser_path else {}))
    for width in [1440, 1024, 768, 390, 320]:
        page = browser.new_page(viewport={'width': width, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' and '503' not in msg.text else None)
        response = page.goto(BASE)
        assert response.status == 200
        assert page.locator('[data-site="public-landing"]').count() == 1
        assert page.locator('#profile-form, #rescue-form').count() == 0
        assert page.get_by_role('heading', level=1).inner_text() == 'The right support.\nA clearer way forward.'
        page.wait_for_function("!document.querySelector('#public-waha-status').textContent.includes('Availability requires')")
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.evaluate('Promise.all(document.getAnimations().map(animation => animation.finished.catch(() => {})))')
        page.screenshot(path=str(OUT / f'landing-{width}.png'), full_page=True)
        # Every displayed copy key has a local Hindi and Marathi translation.
        assert page.evaluate("elements.every(el => Object.hasOwn(translations.hi, el.dataset.i18n) && Object.hasOwn(translations.mr, el.dataset.i18n))")
        for language, heading in [('hi', 'सही सहायता।'), ('mr', 'योग्य मदत.')]:
            page.locator('#site-language').select_option(language)
            assert page.locator('html').get_attribute('lang') == language
            assert heading in page.locator('#hero-title').inner_text()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width, language)
            assert page.locator('#hero-title').evaluate('(el) => getComputedStyle(el).fontFamily.includes("Nirmala")')
            if width in [1440, 390]:
                page.screenshot(path=str(OUT / f'landing-{language}-{width}.png'), full_page=True)
        page.locator('#site-language').select_option('en')
        if width <= 900:
            toggle = page.locator('#menu-toggle')
            assert toggle.get_attribute('aria-label') == 'Open navigation'
            toggle.click()
            assert toggle.get_attribute('aria-expanded') == 'true'
            page.locator('#site-nav a[href="#features"]').click()
            assert toggle.get_attribute('aria-expanded') == 'false'
            toggle.click()
            page.keyboard.press('Escape')
            assert toggle.get_attribute('aria-expanded') == 'false'
            assert toggle.evaluate('(el) => el === document.activeElement')
        page.locator('#faq details').first.locator('summary').click()
        assert page.locator('#faq details').first.get_attribute('open') is not None
        assert page.get_by_text('No. Yojana-Pilot is an independent project,', exact=False).is_visible()
        assert page.locator('a[href="/app"]').first.get_attribute('href') == '/app'
        page.locator('.hero-actions a[href="/app"]').click()
        page.wait_for_url('**/app#assistant')
        page.wait_for_function("document.querySelector('#scheme-id').options[0].value !== ''")
        assert page.locator('#page-title').inner_text() == 'Your benefits dashboard'
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(OUT / f'dashboard-{width}.png'), full_page=True)
        page.get_by_role('button', name='Start a conversation').click()
        assert page.locator('#message').evaluate('(el) => el === document.activeElement')
        # Controlled failure verifies the retry UI without spending model tokens.
        page.route('**/api/v2/conversation', lambda route: route.fulfill(
            status=503, content_type='application/json', body=json.dumps({'detail': 'Test language service outage'})))
        page.locator('#message').fill('Hello')
        page.locator('#send').click()
        page.get_by_role('button', name='Retry', exact=True).wait_for()
        assert page.locator('#message').input_value() == 'Hello'
        page.locator('#reset').click()
        assert page.locator('#stat-facts').inner_text() == '0'
        # Fixture conversation reply exercises rendering. Scheme results come from
        # the real deterministic engine; only model extraction is substituted.
        profile = {'age': 62, 'state': 'maharashtra', 'occupation': 'farmer',
                   'owns_cultivable_land': True, 'is_income_tax_payer': False,
                   'is_excluded_profession': False}
        shortlist = build_shortlist(profile, load_catalog())
        payload = {'session_id': 'browser-fixture', 'profile': profile,
                   'message': 'What is your annual family income?',
                   'question': {'reason': 'Income affects pension eligibility.'},
                   'results': shortlist, 'corrections': {}}
        page.route('**/api/v2/conversation', lambda route: route.fulfill(
            content_type='application/json', body=json.dumps(payload)))
        page.locator('#lang').select_option('mr')
        page.locator('#message').fill('\u092e\u0940 \u0936\u0947\u0924\u0915\u0930\u0940 \u0906\u0939\u0947')
        page.locator('#send').click()
        page.get_by_text('Why I ask: Income affects pension eligibility.', exact=False).wait_for()
        assert page.locator('#stat-facts').inner_text() == str(len(profile))
        assert int(page.locator('#stat-matches').inner_text()) > 0
        page.locator('[data-page="schemes"]').click()
        assert page.locator('#page-schemes').is_visible()
        page.locator('#scheme-list details').first.click()
        assert page.locator('#scheme-list').get_by_text('Documents', exact=True).first.is_visible()
        page.locator('[data-page="rescue"]').click()
        page.locator('#scheme-id').select_option('pm_kisan')
        page.locator('#dep-ekyc_done').select_option('false')
        page.locator('#aadhaar-name').fill('Sunil Patil')
        page.locator('#land-name').fill('Suneel Patil')
        page.locator('#status-text').fill('eKYC pending')
        page.locator('#rescue-send').click()
        page.get_by_text('Government status verified: No.', exact=False).wait_for()
        assert page.locator('#rescue-result').get_by_text('Suggested corrective actions').is_visible()
        assert 'e-KYC' in page.locator('#rescue-result').inner_text() or 'eKYC' in page.locator('#rescue-result').inner_text()
        page.screenshot(path=str(OUT / f'rescue-{width}.png'), full_page=True)
        page.locator('[data-page="channels"]').click()
        page.get_by_text('Voice calling · Upcoming LiveKit', exact=True).wait_for()
        assert 'Checking...' not in page.locator('#waha-status').inner_text()
        for name in ['assistant', 'schemes', 'rescue', 'channels']:
            page.locator(f'[data-page="{name}"]').click()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width, name)
        assert not errors, errors
        page.goto(BASE + '/chat')
        assert '/app' in page.url
        assert page.locator('#page-title').inner_text() == 'Your benefits dashboard'
        for section in ['schemes', 'rescue', 'channels']:
            page.goto(BASE + '/app#' + section)
            assert page.locator('#page-' + section).is_visible()
        page.goto(BASE + '/legacy')
        page.locator('#profile-form input').first.wait_for()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        # Swagger assets are hosted by FastAPI's default CDN. Render the docs
        # once; repeated mobile checks concern our public site and workspace.
        if width == 1440:
            page.goto(BASE + '/docs')
            page.wait_for_function("document.body.innerText.includes('/api/v2/conversation')")
        else:
            assert page.request.get(BASE + '/docs').status == 200
        assert not errors, errors
        print(f'{width}px: landing, Hindi/Marathi, menu/FAQ, app, redirect, deep links, retry/reset, Rescue, channels, legacy/docs; no overflow or unexpected console errors')
        page.close()
    browser.close()

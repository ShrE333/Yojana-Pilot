"""Opt-in WAHA bridge for Yojana-Pilot conversation endpoint.

The signed production path stores mapping/deduplication/outbox state in PostgreSQL.
The legacy URL-secret route is development-only. Only opted-in 1:1 text is handled;
external delivery remains at-least-once and requires live account validation.
"""
from __future__ import annotations
import hmac
import hashlib
import json
import os
import re
import threading
import time
from collections import OrderedDict
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .conversation_api import Message, conversation
from . import case_store
from .privacy import redact
from .channel_security import seal, unseal

router = APIRouter(tags=["WhatsApp (opt-in beta)"])
_guard = threading.RLock()
_chats: dict[str, str] = {}
_seen: OrderedDict[str, float] = OrderedDict()
_MAX_CHATS = 300
_MAX_SEEN = 2000


def _config():
    url = os.getenv("WAHA_BASE_URL", "").rstrip("/")
    key = os.getenv("WAHA_API_KEY", "")
    secret = os.getenv("YOJANA_WAHA_WEBHOOK_SECRET", "") or os.getenv('WAHA_HMAC_KEY', '')
    if not url or not key or len(secret) < 24:
        raise HTTPException(503, "WhatsApp bridge not configured")
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http") or not parsed.netloc:
        raise HTTPException(503, "WAHA_BASE_URL must be an HTTP(S) URL")
    return url, key, secret


class WAHAEvent(BaseModel):
    event: str = ""
    session: str | None = None
    payload: dict = {}
    timestamp: int | None = None


def _send(url: str, key: str, session: str, chat_id: str, message: str):
    resp = httpx.post(url + "/api/sendText",
                      headers={"X-Api-Key": key},
                      json={"session": session, "chatId": chat_id, "text": message[:3500]},
                      timeout=12)
    resp.raise_for_status()


def _duplicate(msg_id: str) -> bool:
    now = time.time()
    with _guard:
        for key, created in list(_seen.items()):
            if now - created > 7200:
                _seen.pop(key, None)
        if msg_id in _seen:
            return True
        _seen[msg_id] = now
        if len(_seen) > _MAX_SEEN:
            _seen.popitem(last=False)
        return False


@router.post("/api/integrations/waha/{webhook_secret}", include_in_schema=False)
def receive_waha(webhook_secret: str, body: WAHAEvent):
    if os.getenv('DEPLOYMENT_MODE') == 'production':
        raise HTTPException(404, 'Use the signed webhook endpoint')
    if os.getenv("YOJANA_WAHA_ENABLED", "false").lower() != "true":
        raise HTTPException(503, "WhatsApp bridge is disabled")
    url, key, secret = _config()
    if not hmac.compare_digest(webhook_secret, secret):
        raise HTTPException(403, "Forbidden")
    expected_session = os.getenv("WAHA_SESSION", "default")
    if body.event != "message" or body.session != expected_session:
        return {"status": "ignored"}
    p = body.payload
    chat_id = p.get("from", "")
    if not isinstance(chat_id, str) or not re.fullmatch(r"\d{6,16}@c\.us", chat_id):
        return {"status": "ignored"}  # never groups, status, newsletters
    if p.get("fromMe") or p.get("hasMedia"):
        return {"status": "ignored"}
    message = p.get("body", "")
    if not isinstance(message, str) or not message.strip():
        return {"status": "ignored"}
    mid = p.get("id")
    if isinstance(mid, dict):
        mid = mid.get("_serialized") or mid.get("id")
    if not isinstance(mid, str) or not mid:
        return {"status": "ignored"}  # reject unidentifiable events to prevent loops
    if _duplicate(f"{expected_session}:{mid}"):
        return {"status": "duplicate"}
    command = message.strip().lower()
    with _guard:
        old = _chats.get(chat_id)
    if command in ("stop", "unsubscribe", "बंद", "थांबा"):
        with _guard:
            _chats.pop(chat_id, None)
        _send(url, key, expected_session, chat_id, "Yojana-Pilot stopped. Send START to opt in again. This does not delete prior service logs.")
        return {"status": "stopped"}
    if command in ("start", "yojana", "योजना", "सुरू"):
        with _guard:
            if len(_chats) >= _MAX_CHATS and chat_id not in _chats:
                raise HTTPException(503, "Chat limit reached")
            _chats[chat_id] = ""
        _send(url, key, expected_session, chat_id,
              "Welcome to Yojana-Pilot! Please describe your situation in English, Hindi or Marathi. Do not send Aadhaar, phone, bank numbers or documents. Reply STOP anytime. This is a demo, not an official government service.")
        return {"status": "started"}
    if old is None:
        return {"status": "not_opted_in"}  # never unsolicited reply
    try:
        result = conversation(Message(text=message, lang="en", session_id=old or None))
    except HTTPException as exc:
        if exc.status_code == 404:  # expired in-memory session: restart on next message
            with _guard:
                _chats[chat_id] = ""
        _send(url, key, expected_session, chat_id,
              "I couldn't process that message. Please try again without sharing sensitive numbers.")
        return {"status": "error", "code": exc.status_code}
    with _guard:
        _chats[chat_id] = result["session_id"]
    _send(url, key, expected_session, chat_id, result["message"])
    return {"status": "replied"}


@router.get("/api/integrations/waha/status")
def waha_status():
    return {"configured": bool(os.getenv("WAHA_BASE_URL") and os.getenv("WAHA_API_KEY")
                              and len(os.getenv("YOJANA_WAHA_WEBHOOK_SECRET", "") or os.getenv('WAHA_HMAC_KEY', '')) >= 24),
            "enabled": os.getenv("YOJANA_WAHA_ENABLED", "false").lower() == "true",
            "mode": "opt-in text only", "production_ready": False,
            'persistent': case_store.enabled(), 'connectivity': 'unverified'}


@router.post('/api/webhooks/waha', include_in_schema=False)
async def signed_webhook(request: Request):
    if os.getenv('YOJANA_WAHA_ENABLED', 'false').lower() != 'true':
        raise HTTPException(503, 'WhatsApp bridge is disabled')
    _config()
    secret = os.getenv('WAHA_HMAC_KEY', '')
    if len(secret) < 32 or not case_store.enabled():
        raise HTTPException(503, 'Signed persistent webhook is not configured')
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 32768:
            raise HTTPException(413, 'Webhook too large')
    signature = request.headers.get('x-webhook-hmac', '')
    if request.headers.get('x-webhook-hmac-algorithm', '').lower() != 'sha512' or not hmac.compare_digest(
            signature, hmac.new(secret.encode(), raw, hashlib.sha512).hexdigest()):
        raise HTTPException(403, 'Invalid webhook signature')
    try:
        body = WAHAEvent.model_validate_json(raw)
    except ValueError:
        raise HTTPException(422, 'Invalid webhook payload')
    if os.getenv('DEPLOYMENT_MODE') == 'production' and (
            body.timestamp is None or abs(time.time() - body.timestamp / 1000) > 86400):
        raise HTTPException(403, 'Webhook timestamp is missing or stale')
    # Provider calls and DBAPI transactions must not block the ASGI event loop.
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(persistent_event, body)


def persistent_event(body):
    expected_session = os.getenv('WAHA_SESSION', 'default')
    p = body.payload
    chat = p.get('from', '')
    text, mid = p.get('body', ''), p.get('id')
    if isinstance(mid, dict):
        mid = mid.get('_serialized') or mid.get('id')
    if (body.event != 'message' or body.session != expected_session or not isinstance(chat, str)
            or not re.fullmatch(r'\d{6,16}@c\.us', chat) or p.get('fromMe') or p.get('hasMedia')
            or not isinstance(text, str) or not text.strip() or not isinstance(mid, str) or not mid):
        return {'status': 'ignored'}
    if len(text) > 1800 or len(mid) > 500:
        raise HTTPException(422, 'Message too large')
    chat_hash = case_store.chat_digest(expected_session + ':' + chat)
    event_id = case_store.chat_digest(expected_session + ':' + mid)
    lock = 'waha:' + chat_hash
    with case_store.transaction(lock) as tx:
        event = tx.execute('SELECT * FROM waha_events WHERE id=?', (event_id,)).fetchone()
        if event and event['chat_hash'] != chat_hash:
            raise HTTPException(409, 'Event identity conflict')
        if not event:
            mapping = tx.execute('SELECT * FROM waha_chats WHERE chat_hash=?', (chat_hash,)).fetchone()
            preference = tx.execute('SELECT mode FROM channel_preferences WHERE chat_hash=?',(chat_hash,)).fetchone()
            awaiting = not preference or preference['mode'] != 'chat'
            command = text.strip().lower()
            reply = None
            if command in ('stop', 'unsubscribe', 'बंद', 'थांबा'):
                # No unsolicited STOP acknowledgement to a chat that never opted in.
                status = 'stopped' if mapping and mapping['opted_in'] else 'not_opted_in'
                if mapping and mapping['opted_in']:
                    if mapping['session_id']:
                        tx.lock(mapping['session_id'])
                        tx.execute('DELETE FROM case_sessions WHERE id=?', (mapping['session_id'],))
                    tx.execute('DELETE FROM waha_chats WHERE chat_hash=?', (chat_hash,))
                    tx.execute("UPDATE waha_events SET status='sent',reply=NULL,result='cancelled' WHERE chat_hash=?", (chat_hash,))
                    reply = 'Yojana-Pilot stopped. The local case was cleared. Send START to opt in again. WAHA/provider logs have separate retention policies.'
            elif command in ('start', 'yojana', 'योजना', 'सुरू'):
                if not mapping or not mapping['opted_in']:
                    tx.execute('''INSERT INTO waha_chats VALUES(?,?,?,?) ON CONFLICT(chat_hash)
                        DO UPDATE SET opted_in=1,updated_at=excluded.updated_at''', (chat_hash, None, 1, time.time()))
                status = 'started'
                tx.execute("INSERT INTO channel_preferences VALUES(?,'awaiting') ON CONFLICT(chat_hash) DO UPDATE SET mode='awaiting'",(chat_hash,))
                reply = 'Welcome to Yojana-Pilot! Choose 1 Chat or 2 Call (a browser voice session, not a phone call). You can also type your question. Reply CALL to switch later. Do not send identity, bank or phone numbers. Reply STOP to leave. Guidance only, not a government service.'
            elif not mapping or not mapping['opted_in']:
                status = 'not_opted_in'
            elif command == 'chat' or (command in ('1', '1 chat') and awaiting):
                tx.execute("INSERT INTO channel_preferences VALUES(?,'chat') ON CONFLICT(chat_hash) DO UPDATE SET mode='chat'",(chat_hash,))
                status, reply = 'chat_selected', 'Chat selected. Describe the help you need and your situation. Reply CALL for browser voice or STOP to leave.'
            elif command == 'call' or (command in ('2', '2 call') and awaiting):
                tx.execute("INSERT INTO channel_preferences VALUES(?,'voice') ON CONFLICT(chat_hash) DO UPDATE SET mode='voice'",(chat_hash,))
                from .voice import new_channel_case, handoff
                sid = mapping['session_id']
                if sid:
                    tx.lock(sid)
                    try:
                        tx.session(sid,trusted=True)
                    except HTTPException as exc:
                        if exc.status_code != 404:
                            raise
                        sid = None
                sid = sid or new_channel_case(tx)
                tx.execute('UPDATE waha_chats SET session_id=?,updated_at=? WHERE chat_hash=?',(sid,time.time(),chat_hash))
                tx.execute('INSERT INTO channel_routes VALUES(?,?) ON CONFLICT(chat_hash) DO UPDATE SET address_cipher=excluded.address_cipher',
                           (chat_hash,seal(chat)))
                link = handoff(tx,sid,chat_hash)
                status,reply = 'voice_link', 'Open your private one-use browser voice link within 10 minutes. Do not forward it:\n'+link
            else:
                tx.execute("INSERT INTO channel_preferences VALUES(?,'chat') ON CONFLICT(chat_hash) DO UPDATE SET mode='chat'",(chat_hash,))
                sid = mapping['session_id']
                if sid:
                    try:
                        tx.session(sid, trusted=True)
                    except HTTPException as exc:
                        if exc.status_code != 404:
                            raise
                        sid = None
                try:
                    result = conversation(Message(text=text, session_id=sid, request_id=event_id), trusted=True, tx=tx)
                except HTTPException as exc:
                    if exc.status_code in (429, 503):
                        # Roll back: WAHA's retry may safely process this event later.
                        raise
                    status, reply = 'rejected', 'Please retry without sharing identity, bank or phone numbers.'
                else:
                    tx.execute('UPDATE waha_chats SET session_id=?,updated_at=? WHERE chat_hash=?',
                               (result['session_id'], time.time(), chat_hash))
                    status, reply = 'replied', redact(result['message'])
            now = time.time()
            tx.execute('INSERT INTO waha_events VALUES(?,?,?,?,?,?,?)',
                       (event_id, chat_hash, 'pending' if reply else 'sent', status,
                        seal(reply) if reply and '#handoff=' in reply else reply, now, now))
    # The response outbox is committed before delivery. A retry reuses it without
    # repeating extraction, fact mutation or opt-in transitions.
    with case_store.transaction(lock) as tx:
        event = tx.execute('SELECT * FROM waha_events WHERE id=?', (event_id,)).fetchone()
        if event['status'] == 'sent':
            return {'status': 'duplicate' if event['reply'] else event['result']}
        url, key, _ = _config()
        try:
            _send(url, key, expected_session, chat, unseal(event['reply']) if event['result']=='voice_link' else event['reply'])
        except (httpx.HTTPError, OSError):
            raise HTTPException(503, 'Delivery pending; retry this event')
        tx.execute('UPDATE waha_events SET status=?,updated_at=? WHERE id=?', ('sent', time.time(), event_id))
        return {'status': event['result']}

"""Public webhooks for the sales agent. They are open to the internet, so each one checks the provider's signature."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from urllib.parse import parse_qsl

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response

from app.salesbot import lang as L
from app.salesbot.channels import meta, voice

log = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks")


def _secret(v) -> str:
    return v.get_secret_value() if v is not None else ""


def _agent(request: Request):
    agent = getattr(request.app.state, "sales", None)
    if agent is None:
        raise HTTPException(503, "sales agent disabled")
    return agent


# ------------------------------------------------------------------ Meta: Instagram / WhatsApp / Messenger
@router.get("/meta")
async def meta_verify(request: Request) -> PlainTextResponse:
    q = request.query_params
    expected = _secret(request.app.state.settings.meta_verify_token)
    if q.get("hub.mode") == "subscribe" and expected and hmac.compare_digest(q.get("hub.verify_token", ""), expected):
        return PlainTextResponse(q.get("hub.challenge", ""))
    raise HTTPException(403, "verification failed")


async def _process_meta(agent, sender: meta.MetaSender, items: list[meta.Incoming]) -> None:
    for it in items:
        try:
            reply = await agent.handle(it.channel, it.sender, it.text, display_name=it.name, phone=it.phone, message_id=it.message_id)
            if reply.text:
                await sender.send(it.channel, it.sender, reply.text)
        except Exception:
            log.exception("Meta хабарини қайта ишлашда хато (%s)", it.channel)


@router.post("/meta")
async def meta_receive(request: Request, background: BackgroundTasks) -> dict:
    agent = _agent(request)
    body = await request.body()
    if not meta.verify_signature(_secret(request.app.state.settings.meta_app_secret), body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(403, "bad signature")
    try:
        items = meta.parse(json.loads(body))
    except ValueError:
        raise HTTPException(400, "bad json")
    # Meta expects a fast 200; replies are sent after the response.
    background.add_task(_process_meta, agent, request.app.state.meta_sender, items)
    return {"received": len(items)}


# ------------------------------------------------------------------ Twilio voice
async def _twilio_params(request: Request) -> dict[str, str]:
    settings = request.app.state.settings
    params = dict(parse_qsl((await request.body()).decode(), keep_blank_values=True))
    base = (settings.sales_public_base_url or str(request.base_url)).rstrip("/")
    url = base + request.url.path + (f"?{request.url.query}" if request.url.query else "")
    if not voice.verify_signature(_secret(settings.twilio_auth_token), url, params, request.headers.get("X-Twilio-Signature")):
        raise HTTPException(403, "bad signature")
    return params


def _xml(text: str) -> Response:
    return Response(text, media_type="application/xml")


@router.post("/twilio/voice")
async def twilio_voice(request: Request) -> Response:
    agent = _agent(request)
    p = await _twilio_params(request)
    caller = p.get("From") or p.get("CallSid", "unknown")
    reply = await agent.handle("phone", caller, "", phone=caller, message_id=f"{p.get('CallSid')}:start")
    lg = reply.language
    text = reply.text or L.text(lg, "greet_voice", shop=request.app.state.settings.sales_shop_name)
    return _xml(voice.gather_twiml(text, request.app.state.settings, "/webhooks/twilio/voice/gather", L.text(lg, "noinput_voice")))


@router.post("/twilio/voice/gather")
async def twilio_gather(request: Request) -> Response:
    agent = _agent(request)
    settings = request.app.state.settings
    p = await _twilio_params(request)
    caller = p.get("From") or p.get("CallSid", "unknown")
    speech = (p.get("SpeechResult") or "").strip()
    action = "/webhooks/twilio/voice/gather"
    if not speech:
        lg = await agent.language_of("phone", caller)
        return _xml(voice.gather_twiml(L.text(lg, "noinput_voice"), settings, action, L.text(lg, "noinput_voice")))
    # Twilio retries a webhook with the same CallSid and phrase; dedup on that.
    digest = hashlib.sha1(f"{p.get('CallSid')}:{p.get('SequenceNumber', '')}:{speech}".encode()).hexdigest()[:20]
    reply = await agent.handle("phone", caller, speech, phone=caller, message_id=f"{p.get('CallSid')}:{digest}")
    lg = reply.language
    if reply.done:
        return _xml(voice.hangup_twiml(reply.text or L.text(lg, "handoff"), settings, L.text(lg, "bye_voice")))
    return _xml(voice.gather_twiml(reply.text or L.text(lg, "noinput_voice"), settings, action, L.text(lg, "noinput_voice")))

"""Public webhooks for the sales agent. They are open to the internet, so each one checks the provider's signature."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
from collections import defaultdict, deque
from pathlib import Path
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
            if it.comment_id:
                await _process_comment(agent, sender, it)
                continue
            if it.attachment:
                limit = agent.settings.sales_max_file_mb * 1024 * 1024
                data, too_big = await sender.download(it, limit)
                reply = await agent.handle_file(it.channel, it.sender, kind=it.attachment["kind"], data=data, too_big=too_big,
                                                file_name=it.attachment.get("name"), mime=it.attachment.get("mime"), caption=it.text or None,
                                                message_id=it.message_id, display_name=it.name, phone=it.phone)
            else:
                reply = await agent.handle(it.channel, it.sender, it.text, display_name=it.name, phone=it.phone, message_id=it.message_id,
                                           source=it.source)
            if reply.text:
                await sender.send(it.channel, it.sender, reply.text)
        except Exception:
            log.exception("Meta хабарини қайта ишлашда хато (%s)", it.channel)


async def _process_comment(agent, sender: meta.MetaSender, it: meta.Incoming) -> None:
    import random

    if not await agent.comment_wants_reply(it.text):
        return
    reply = await agent.handle("instagram", it.sender, it.text, display_name=it.name, message_id=it.message_id)
    if not reply.text:
        return  # duplicate webhook
    lg = reply.language
    if await sender.private_reply(it.comment_id, reply.text):
        public = random.choice(L.text(lg, "comment_public").split("|"))
    else:
        public = L.text(lg, "comment_public_nodm")
    await sender.comment_reply(it.comment_id, public)


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


# ------------------------------------------------------------------ site chat widget
_SESSION = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_hits: dict[str, deque] = defaultdict(deque)
WIDGET = Path(__file__).with_name("channels") / "web_widget.js"


def _cors(request: Request, response: Response) -> Response:
    allowed = [o.strip() for o in (request.app.state.settings.sales_web_origins or "").split(",") if o.strip()]
    origin = request.headers.get("origin")
    if "*" in allowed:
        response.headers["Access-Control-Allow-Origin"] = "*"
    elif origin and origin in allowed:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


def _limited(request: Request, session: str, per_minute: int = 30) -> bool:
    """Sliding one-minute window per session and per IP: a public endpoint must not be a free AI proxy."""
    now = time.monotonic()
    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
    for key in (f"s:{session}", f"ip:{ip}"):
        q = _hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= (per_minute if key.startswith("s:") else per_minute * 4):
            return True
        q.append(now)
    if len(_hits) > 50_000:
        _hits.clear()
    return False


@router.options("/web/chat")
@router.options("/web/poll")
async def web_preflight(request: Request) -> Response:
    return _cors(request, Response(status_code=204))


@router.post("/web/chat")
async def web_chat(request: Request) -> Response:
    agent = _agent(request)
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "bad json")
    session, text = str(body.get("session") or ""), str(body.get("text") or "")[:1000]
    if not _SESSION.match(session):
        raise HTTPException(400, "bad session")
    if _limited(request, session):
        return _cors(request, Response(json.dumps({"error": "slow down"}), status_code=429, media_type="application/json"))
    src = body.get("source") if isinstance(body.get("source"), dict) else None
    source = {"type": "link", "id": str(src.get("campaign") or src.get("source") or "")[:64], "name": str(src.get("source") or "")[:64] or None} if src and (src.get("campaign") or src.get("source")) else None
    n = body.get("n")
    reply = await agent.handle("web", session, text, message_id=f"web:{session}:{int(n)}" if isinstance(n, int) else None, source=source)
    msgs = await agent.web_messages(session, 0)
    payload = {"reply": reply.text, "last_id": msgs[-1]["id"] if msgs else 0}
    return _cors(request, Response(json.dumps(payload, ensure_ascii=False), media_type="application/json"))


@router.get("/web/poll")
async def web_poll(request: Request, session: str, after: int = 0) -> Response:
    agent = _agent(request)
    if not _SESSION.match(session):
        raise HTTPException(400, "bad session")
    if _limited(request, session, per_minute=40):
        return _cors(request, Response("[]", status_code=429, media_type="application/json"))
    msgs = await agent.web_messages(session, after)
    return _cors(request, Response(json.dumps(msgs, ensure_ascii=False), media_type="application/json"))


@router.get("/web/widget.js")
async def web_widget(request: Request) -> Response:
    js = WIDGET.read_text(encoding="utf-8").replace("__SHOP__", json.dumps(request.app.state.settings.sales_shop_name, ensure_ascii=False))
    return Response(js, media_type="application/javascript; charset=utf-8", headers={"Cache-Control": "public, max-age=300"})

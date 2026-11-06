"""Web panel: /panel (page) and /api/panel/* (data and actions, X-API-Token required).
Visual schemes: /visual/ (static pages from the visual/ folder, demo data only, no client data).

The API listens on 127.0.0.1 on the laptop and the Cloudflare tunnel exposes
only /webhooks/, so the panel is reachable only from the laptop itself.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from app.api.routes import require_token
from app.panel import service
from app.salesbot.models import LeadFile
from app.security import audit

PAGE = Path(__file__).with_name("panel.html")
VISUAL = Path(__file__).resolve().parents[2] / "visual"

page = APIRouter()
panel = APIRouter(prefix="/api/panel", dependencies=[Depends(require_token)])


@page.get("/panel", response_class=HTMLResponse)
async def panel_page() -> HTMLResponse:
    return HTMLResponse(PAGE.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


@page.get("/visual", include_in_schema=False)
async def visual_root() -> RedirectResponse:
    return RedirectResponse("/visual/")


@page.get("/visual/", response_class=HTMLResponse)
@page.get("/visual/{name}", response_class=HTMLResponse)
async def visual_page(name: str = "index.html") -> HTMLResponse:
    pages = {p.name for p in VISUAL.glob("*.html")}  # only the top folder: templates/ and anything else stay out
    if name not in pages:
        raise HTTPException(404, "Бундай саҳифа йўқ")
    return HTMLResponse((VISUAL / name).read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


def _sales(request: Request):
    agent = getattr(request.app.state, "sales", None)
    if agent is None:
        raise HTTPException(503, "Сотув агенти ўчирилган")
    return agent


@panel.get("/connections")
async def connections(request: Request) -> list[dict]:
    async with request.app.state.session_factory() as s:
        return await service.connections(s, request.app.state.settings)


@panel.get("/director")
async def director(request: Request, days: int = 30) -> dict:
    async with request.app.state.session_factory() as s:
        return await service.director(s, request.app.state.settings, max(1, min(days, 365)))


@panel.get("/agent")
async def agent_process(request: Request) -> dict:
    async with request.app.state.session_factory() as s:
        return await service.agent_process(s, request.app.state.settings)


@panel.get("/manager")
async def manager(request: Request) -> dict:
    async with request.app.state.session_factory() as s:
        return await service.manager(s, request.app.state.settings)


@panel.get("/leads/{lead_id}")
async def lead(request: Request, lead_id: int) -> dict:
    async with request.app.state.session_factory() as s:
        data = await service.lead_detail(s, request.app.state.settings, lead_id)
    if data is None:
        raise HTTPException(404, "Лид топилмади")
    return data


@panel.get("/files/{file_id}")
async def file(request: Request, file_id: int):
    async with request.app.state.session_factory() as s:
        f = await s.get(LeadFile, file_id)
    if f is None or not f.path:
        raise HTTPException(404, "Файл йўқ")
    path = Path(f.path).resolve()
    settings = request.app.state.settings
    root = Path(settings.sales_files_dir).resolve()
    if not path.is_relative_to(root) or not path.exists():
        raise HTTPException(404, "Файл йўқ")
    from app.salesbot import media

    key = settings.sales_files_key.get_secret_value() if settings.sales_files_key else None
    try:
        data = media.read(path, key)
    except Exception:
        raise HTTPException(500, "Файлни очиб бўлмади (шифрлаш калити?)") from None
    await _audit(request, "file.viewed", f.lead_id, {"file": f.file_name})
    from urllib.parse import quote

    return Response(data, media_type=f.mime or "application/octet-stream", headers={
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(f.file_name)}", "Cache-Control": "no-store"})


class TextIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ResultIn(BaseModel):
    outcome: str
    note: str = Field(default="", max_length=500)


async def _audit(request: Request, action: str, lead_id: int, details: dict | None = None) -> None:
    async with request.app.state.session_factory() as s:
        await audit.log_action(s, action, None, "lead", lead_id, {"via": "panel", **(details or {})})
        await s.commit()


def _result(ok: bool, info: str) -> dict:
    return {"ok": ok, "message": info}


@panel.post("/leads/{lead_id}/reply")
async def reply(request: Request, lead_id: int, body: TextIn) -> dict:
    ok, info = await _sales(request).manager_reply(lead_id, body.text)
    if ok:
        await _audit(request, "lead.manager_reply", lead_id)
    return _result(ok, info)


@panel.post("/leads/{lead_id}/answer")
async def answer(request: Request, lead_id: int, body: TextIn) -> dict:
    ok, info = await _sales(request).manager_answer(lead_id, body.text)
    if ok:
        await _audit(request, "lead.answer", lead_id)
    return _result(ok, info)


@panel.post("/leads/{lead_id}/paid")
async def paid(request: Request, lead_id: int) -> dict:
    ok, info = await _sales(request).mark_paid(lead_id)
    if ok:
        await _audit(request, "lead.paid", lead_id)
    return _result(ok, info)


class StatusIn(BaseModel):
    step: int = Field(ge=0, le=4)
    note: str = Field(default="", max_length=300)


class DocIn(BaseModel):
    doc_id: str = Field(min_length=1, max_length=40)
    received: bool


@panel.post("/leads/{lead_id}/status")
async def status(request: Request, lead_id: int, body: StatusIn) -> dict:
    ok, info = await _sales(request).set_status(lead_id, body.step, body.note)
    if ok:
        await _audit(request, "lead.status", lead_id, {"step": body.step + 1})
    return _result(ok, info)


@panel.post("/leads/{lead_id}/docs")
async def docs(request: Request, lead_id: int, body: DocIn) -> dict:
    ok, info = await _sales(request).set_doc(lead_id, body.doc_id, body.received)
    if ok:
        await _audit(request, "lead.doc", lead_id, {"doc": body.doc_id, "received": body.received})
    return _result(ok, info)


@panel.get("/leads/{lead_id}/doc/{kind}")
async def lead_pdf(request: Request, lead_id: int, kind: str):
    if kind not in ("invoice", "contract"):
        raise HTTPException(404, "Ҳужжат йўқ")
    docs = await _sales(request).build_documents(lead_id)
    name = f"{'hisob' if kind == 'invoice' else 'shartnoma'}_{lead_id}.pdf"
    if name not in docs:
        raise HTTPException(404, "PDF тузилмади (шрифт ёки шаблон йўқ)")
    return Response(docs[name], media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}"'})


@panel.post("/leads/{lead_id}/result")
async def result(request: Request, lead_id: int, body: ResultIn) -> dict:
    ok, info = await _sales(request).send_result(lead_id, body.outcome, body.note)
    if ok:
        await _audit(request, "lead.result", lead_id, {"outcome": body.outcome})
    return _result(ok, info)

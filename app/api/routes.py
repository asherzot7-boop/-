"""Optional REST API: health check and read-only KPI.

/health is public. /api/* requires the X-API-Token header equal to
API_TOKEN; if API_TOKEN is not set, /api/* is disabled.
"""
from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from sqlalchemy import select, text

from app.database.models import Book
from app.services import approvals, reporting, tasks


def require_token(request: Request, x_api_token: str | None = Header(default=None)) -> None:
    expected = request.app.state.settings.api_token
    if expected is None or not expected.get_secret_value():
        raise HTTPException(status_code=403, detail="API disabled: set API_TOKEN")
    if not x_api_token or not hmac.compare_digest(x_api_token, expected.get_secret_value()):
        raise HTTPException(status_code=401, detail="Invalid token")


router = APIRouter()
api = APIRouter(prefix="/api", dependencies=[Depends(require_token)])


@router.get("/health")
async def health(request: Request) -> dict:
    try:
        async with request.app.state.session_factory() as s:
            await s.execute(text("SELECT 1"))
        db = "ok"
    except Exception:
        db = "error"
    return {"status": "ok" if db == "ok" else "degraded", "database": db, "demo_mode": request.app.state.settings.demo_mode, "llm": request.app.state.settings.llm_enabled}


@api.get("/kpi")
async def kpi(request: Request, period: str = "day") -> dict:
    if period not in reporting.PERIODS:
        raise HTTPException(400, "period: day|week|month")
    async with request.app.state.session_factory() as s:
        return (await reporting.build_kpi(s, period, request.app.state.settings.tz)).to_dict()


@api.get("/tasks")
async def list_tasks(request: Request) -> list[dict]:
    async with request.app.state.session_factory() as s:
        rows = await tasks.open_tasks(s)
        return [{"id": t.id, "title": t.title, "status": t.status.value, "agent": t.assigned_agent, "due_at": t.due_at.isoformat() if t.due_at else None} for t in rows]


@api.get("/books")
async def list_books(request: Request) -> list[dict]:
    async with request.app.state.session_factory() as s:
        rows = await s.scalars(select(Book).order_by(Book.id))
        return [{"id": b.id, "title": b.title, "stage": b.stage.value, "print_run": b.print_run, "price": b.price} for b in rows]


@api.get("/approvals")
async def list_approvals(request: Request) -> list[dict]:
    async with request.app.state.session_factory() as s:
        return [{"id": a.id, "kind": a.kind.value, "title": a.title, "created_at": a.created_at.isoformat()} for a in await approvals.pending(s)]


def create_api(settings, session_factory) -> FastAPI:
    app = FastAPI(title="Nashriyot AI", version="0.1.0", docs_url="/docs" if settings.api_token else None, redoc_url=None)
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.include_router(router)
    app.include_router(api)
    return app


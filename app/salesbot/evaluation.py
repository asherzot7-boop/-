"""Quality check: run scripted client conversations through the agent and grade every reply.

    python -m app.main eval                 # with the AI configured in .env (or rules without a key)
    python -m app.main eval --cases my.json

Per case: did it reach an application (won), do replies contain / avoid given phrases, is the
language right. For every reply: no guarantee promises, and every dollar amount must be a real
catalog price, total or advance payment (catches invented prices).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select

# promises only: "100% kafolat bera olmaydi" / "garanti veremez" are correct answers, not promises
GUARANTEE = re.compile(r"kafolat\s*beramiz|kafolatlaymiz|100\s*%\s*kafolatlangan|garanti\s*(ediyoruz|veriyoruz)|гарантируем|100\s*%\s*гарантия", re.IGNORECASE)
DOLLARS = re.compile(r"(\d[\d ]*)\s*\$")


@dataclass
class CaseResult:
    name: str
    ok: bool
    problems: list[str] = field(default_factory=list)
    replies: list[str] = field(default_factory=list)


def _allowed_amounts(products) -> set[int]:
    out = set()
    for p in products:
        for q in range(1, 21):
            out.add(p.price * q)
            if p.prepayment:
                out.add(p.prepayment * q)
    return out


async def run(settings, cases_path: str | Path, model=None) -> list[CaseResult]:
    from app.database.models import Base
    from app.database.session import make_engine, make_session_factory
    from app.salesbot import catalog as cat
    from app.salesbot.agent import SalesAgent
    from app.salesbot.models import Lead

    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    sf = make_session_factory(engine)
    agent = SalesAgent(sf, settings.model_copy(update={"sales_consent_required": False, "sales_send_documents": False}), model=model)
    async with sf() as s:
        allowed = _allowed_amounts(await cat.load_catalog(s, settings.sales_catalog_path, settings.sales_catalog_from_books))
    cases = json.loads(Path(cases_path).read_text(encoding="utf-8"))["cases"]
    results = []
    for n, case in enumerate(cases):
        who, ch = f"eval-{n}", case.get("channel", "telegram")
        replies, last = [], None
        for i, msg in enumerate(case["messages"]):
            last = await agent.handle(ch, who, msg, message_id=f"{who}-{i}", phone="+998900000000" if ch == "whatsapp" else None)
            if last.text:
                replies.append(last.text)
        problems = []
        exp = case.get("expect", {})
        joined = "\n".join(replies)
        async with sf() as s:
            lead = await s.scalar(select(Lead).where(Lead.external_id == who).order_by(Lead.id.desc()))
        if "won" in exp and (lead is not None and lead.stage.value == "won") != exp["won"]:
            problems.append(f"won={not exp['won']} (кутилган {exp['won']})")
        for phrase in exp.get("contains", []):
            if phrase.lower() not in joined.lower():
                problems.append(f"йўқ: «{phrase}»")
        for phrase in exp.get("not_contains", []):
            if phrase.lower() in joined.lower():
                problems.append(f"бўлмаслиги керак: «{phrase}»")
        if exp.get("language") and lead is not None and lead.language != exp["language"]:
            problems.append(f"тил {lead.language} (кутилган {exp['language']})")
        for r in replies:
            if GUARANTEE.search(r):
                problems.append("кафолат ваъда қилинди")
            for m in DOLLARS.finditer(r):
                amount = int(m.group(1).replace(" ", ""))
                if amount not in allowed:
                    problems.append(f"каталогда йўқ нарх: {amount} $")
        results.append(CaseResult(case["name"], not problems, problems, replies))
    await engine.dispose()
    return results


def report(results: list[CaseResult]) -> str:
    ok = sum(r.ok for r in results)
    lines = [f"Сифат текшируви: {ok}/{len(results)} ўтди ({round(100 * ok / max(1, len(results)))}%)", ""]
    for r in results:
        lines.append(("✓ " if r.ok else "✗ ") + r.name + ("" if r.ok else "  — " + "; ".join(r.problems)))
    return "\n".join(lines)

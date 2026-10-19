"""Production agent: book projects, stages, print run, printing house."""
from __future__ import annotations

import re
from datetime import date

from app.agents.base import AgentContext, AgentResult, BaseAgent
from app.agents.textutil import extract_id, extract_title
from app.database.models import ApprovalKind, BookStage
from app.services import approvals, books

_CREATE = re.compile(r"\b(оч|оч[иі]ш|яра?т|янги\s+\w*\s*лойиҳа|loyiha\w*\s+och|yangi)", re.IGNORECASE)
_PRINT = re.compile(r"босмахона|босмага|bosmaxona|bosmaga|чоп\s+эт", re.IGNORECASE)
_RUN = re.compile(r"(\d[\d\s]*)\s*(?:нусха|дона|tiraj|nusxa|тираж)", re.IGNORECASE)


class ProductionAgent(BaseAgent):
    name = "production"
    title = "Ишлаб чиқариш"
    description = "Қўлёзмадан тайёр маҳсулотгача босқичлар, тираж, муддат ва босмахона."

    async def _resolve_book(self, ctx: AgentContext):
        book_id = ctx.shared.get("book_id") or extract_id(ctx.instruction)
        return await books.get_book(ctx.session, book_id) if book_id else None

    async def handle(self, ctx: AgentContext) -> AgentResult:
        text = ctx.instruction
        low = text.lower()

        if _PRINT.search(low):
            book = await self._resolve_book(ctx)
            if book is None:
                return AgentResult(self.name, "Қайси китоб? Рақамини ёзинг, масалан: «#3 китобни босмахонага юбор».", decision="китоб аниқланмади")
            approval, _ = await approvals.request_approval(
                ctx.session, ApprovalKind.FINAL_PRINT, f"«{book.title}» китобини босмахонага юбориш",
                {"book_id": book.id, "print_run": book.print_run, "printing_house": book.printing_house},
                requested_by=ctx.user, agent=self.name, task=ctx.task,
                idempotency_key=f"final_print:task:{ctx.task.id}" if ctx.task else None,
            )
            return AgentResult(
                self.name,
                f"«{book.title}» босмага тайёр. Якуний файл раҳбар тасдиғисиз юборилмайди: тасдиқ сўрови #{approval.id} яратилди (/approve).",
                data={"book_id": book.id, "approval_id": approval.id}, decision="раҳбар тасдиғи сўралди", approval_ids=[approval.id],
            )

        for name, stage in books.STAGE_BY_NAME.items():
            if stage != BookStage.PRINTING and re.search(rf"\b{name}\w*\s+(?:босқич|bosqich)", low):
                book = await self._resolve_book(ctx)
                if book:
                    await books.set_stage(ctx.session, book, stage)
                    return AgentResult(self.name, f"«{book.title}» — «{books.STAGE_NAMES[stage]}» босқичига ўтказилди.", data={"book_id": book.id, "stage": stage.value}, decision=f"босқич: {stage.value}")

        if _CREATE.search(low) and ("лойиҳа" in low or "loyiha" in low or "китоб" in low or "дарслик" in low or "kitob" in low):
            kind = "textbook" if ("дарслик" in low or "darslik" in low) else "book"
            title = extract_title(text) or (f"Янги дарслик лойиҳаси {date.today():%d.%m.%Y}" if kind == "textbook" else f"Янги китоб лойиҳаси {date.today():%d.%m.%Y}")
            run_m = _RUN.search(text)
            book = await books.create_book(
                ctx.session, title, kind=kind,
                print_run=int(run_m.group(1).replace(" ", "")) if run_m else None,
                target_audience=ctx.shared.get("audience"),
                manuscript_text=ctx.shared.get("text"),
            )
            ctx.shared["book_id"] = book.id
            if ctx.task is not None:
                ctx.task.book_id = book.id
            return AgentResult(
                self.name, f"Янги лойиҳа очилди: #{book.id} «{book.title}», босқич — қўлёзма.",
                data={"book_id": book.id, "kind": kind}, decision="лойиҳа яратилди",
            )

        counts = await books.count_by_stage(ctx.session)
        lines = ["Ишлаб чиқариш ҳолати:"] + [f"• {books.STAGE_NAMES[s]}: {n}" for s, n in counts.items()]
        return AgentResult(self.name, "\n".join(lines), data={s.value: n for s, n in counts.items()}, decision="ҳолат ҳисоботи")

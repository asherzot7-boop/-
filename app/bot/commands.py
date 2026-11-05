"""Command logic, independent of aiogram.

The Telegram handlers, the console demo and the tests all call
CommandService.dispatch(), so behaviour is identical everywhere.
"""
from __future__ import annotations

import logging
import shlex
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.coordinator import Coordinator
from app.config import Settings
from app.database.models import AgentRun, ApprovalKind, BookStage, Role, Task, User
from app.security import audit
from app.security.permissions import ROLE_NAMES, AccessDenied, check, get_active_user, required_role
from app.services import approvals, books, finance, orders, reporting, settings_store, tasks
from app.services.approvals import KIND_NAMES, AlreadyDecided
from app.services.books import STAGE_BY_NAME, STAGE_NAMES, ApprovalRequired
from app.services.finance import fmt_money

log = logging.getLogger(__name__)
TG_LIMIT = 4000


@dataclass
class Reply:
    text: str
    buttons: list[list[tuple[str, str]]] = field(default_factory=list)  # rows of (label, callback_data)


HELP = """Nashriyot AI — буйруқлар

/task <матн> — вазифа яратиш (Координатор агентларга тақсимлайди)
/today — бугунги ишлар
/books — китоблар реестри
   /books new «Номи» · /books stage <id> <босқич> · /books stock <id> <сон>
   /books text <id> <матн> · /books run <id> <тираж> · /books price <id> <нарх> (раҳбар)
/orders — буюртмалар
   /orders new <мижоз_id> <китоб_id> <сон> [нарх] · /orders pay <id> <сумма>
   /orders discount <id> <фоиз> (тасдиқ сўрови)
/clients — мижозлар · /clients new Номи | телефон
/finance [day|week|month] — молиявий ҳисобот
   /finance expense <сумма> <тоифа> [#китоб] · /finance estimate <сумма> <тоифа> [#китоб]
   /finance revenue <сумма> [изоҳ] · /finance cost <китоб_id> · /finance payment <сумма> <кимга>
/report [day|week|month] — умумий ҳисобот (KPI)
/approve — тасдиқ кутаётган қарорлар (раҳбар)
/agents — агентлар ҳолати
/ai <мақсад> — агентли режим: AI асбобларни ўзи чақиради · /flow [id] — жараён қадамлари
/pause · /resume — автоматик жараёнлар (раҳбар)
/leads — сотув агенти лидлари (Instagram, WhatsApp, Telegram, телефон) · /lead <id> — ёзишма · /lead bot <id> — ботга қайтариш
/reply <id> <матн> — мижозга жавоб (у ёзган каналга) · /paid <id> — олдиндан тўлов келди · /files <id> — мижоз юборган ҳужжатлар
/weekly — ҳафталик ҳисобот · /status <id> <1-5> [изоҳ] — ариза ҳолати (мижозга хабар боради) · /answer <id> <жавоб> — савол-жавоб (базага қўшилади) · /faq — ўрганилган жавоблар · /result <id> ban|ok — паспорт текшируви натижаси ва кейинги таклиф

Оддий тилда ҳам ёзишингиз мумкин, масалан:
«Янги дарслик лойиҳасини оч, биринчи бобни муҳаррирга бер, кейин методистга текширтир ва натижани менга юбор»"""

BOOK_KIND = {"textbook": "дарслик", "book": "китоб", "manual": "қўлланма"}
AGENT_TITLES = {"coordinator": "Координатор", "editor": "Муҳаррир", "methodist": "Методист", "production": "Ишлаб чиқариш", "sales": "Сотув", "finance": "Молия"}


def _int(value: str, what: str) -> int:
    try:
        return int(value.replace(" ", "").replace("#", ""))
    except (ValueError, AttributeError):
        raise ValueError(f"{what} рақам бўлиши керак: «{value}»") from None


def _split(args_text: str) -> list[str]:
    try:
        return shlex.split(args_text.replace("«", '"').replace("»", '"'))
    except ValueError:
        return args_text.split()


def chunks(text: str, limit: int = TG_LIMIT) -> list[str]:
    out, cur = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            out.append(cur + line[: limit - len(cur)])
            line, cur = line[limit - len(cur):], ""
        if len(cur) + len(line) > limit:
            out.append(cur)
            cur = ""
        cur += line
    if cur or not out:
        out.append(cur)
    return out


class CommandService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], coordinator: Coordinator, settings: Settings, runtime=None) -> None:
        self.sf = session_factory
        self.coordinator = coordinator
        self.runtime = runtime  # app.agentic.runtime.AgentRuntime, optional
        self.settings = settings

    @property
    def tz(self) -> ZoneInfo:
        return self.settings.tz

    def _local(self, dt: datetime | None) -> str:
        if dt is None:
            return "—"
        return dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(self.tz).strftime("%d.%m.%Y %H:%M")

    # ------------------------------------------------------------------ entry points
    async def dispatch(self, telegram_id: int, text: str, idempotency_key: str | None = None, full_name: str | None = None) -> Reply:
        text = (text or "").strip()
        if not text:
            return Reply("Бўш хабар.")
        async with self.sf() as session:
            user = await get_active_user(session, telegram_id)
            if user is None:
                await audit.log_action(session, "access.denied", telegram_id=telegram_id, details={"text": text[:100]})
                await session.commit()
                return Reply(f"⛔ Рухсат йўқ. Сизнинг Telegram ID: {telegram_id}. Раҳбардан рўйхатга қўшишни сўранг.")
            if full_name and user.full_name != full_name:
                user.full_name = full_name[:200]

            if text.startswith("/"):
                head, _, rest = text.partition(" ")
                command = head[1:].split("@")[0].lower()
                args_text = rest.strip()
            else:
                command = "ai" if self.runtime is not None and self.settings.agent_engine == "agentic" else "task"
                args_text = text
            args = _split(args_text)
            handler = getattr(self, f"cmd_{command}", None)
            if handler is None:
                return Reply("Номаълум буйруқ. /help")
            try:
                check(user, required_role(command, args))
                reply = await handler(session, user, args, args_text, idempotency_key)
                await session.commit()
                return reply
            except AccessDenied as exc:
                await session.rollback()
                async with self.sf() as s2:
                    await audit.log_action(s2, "access.denied", telegram_id=telegram_id, details={"command": command})
                    await s2.commit()
                return Reply(f"⛔ {exc}")
            except (ValueError, LookupError, ApprovalRequired) as exc:
                await session.rollback()
                return Reply(f"⚠️ {exc}")
            except Exception:
                await session.rollback()
                log.exception("Буйруқ хатоси: /%s", command)
                return Reply("❗ Ички хатолик юз берди. Маълумотлар ўзгармади. Кейинроқ қайта уриниб кўринг.")

    async def decide(self, telegram_id: int, approval_id: int, approve: bool) -> Reply:
        async with self.sf() as session:
            user = await get_active_user(session, telegram_id)
            if user is None:
                await audit.log_action(session, "access.denied", telegram_id=telegram_id, details={"approval_id": approval_id})
                await session.commit()
                return Reply("⛔ Рухсат йўқ.")
            try:
                a = await approvals.decide(session, approval_id, user, approve)
                await session.commit()
            except AccessDenied as exc:
                await session.commit()  # keep the audit record of the denied attempt
                return Reply(f"⛔ {exc}")
            except AlreadyDecided as exc:
                await session.rollback()
                return Reply(f"ℹ️ {exc}. Такрорий босиш эътиборга олинмади.")
            except LookupError as exc:
                await session.rollback()
                return Reply(f"⚠️ {exc}")
            except Exception:
                await session.rollback()
                log.exception("Тасдиқлаш хатоси")
                return Reply("❗ Ички хатолик. Қарор сақланмади.")
            mark = "✅ Тасдиқланди" if approve else "🚫 Рад этилди"
            text = f"{mark}: #{a.id} {a.title}\n{a.execution_result or ''}"
            wf_id = (a.payload or {}).get("workflow_id")
            if wf_id:
                from app.agentic.models import Workflow

                wf = await session.get(Workflow, wf_id)
                if wf is not None:
                    text += "\n\n" + self._workflow_text(wf)
            return Reply(text)

    # ------------------------------------------------------------------ commands
    async def cmd_start(self, s, user: User, args, raw, key) -> Reply:
        return Reply(f"Ассалому алайкум{', ' + user.full_name if user.full_name else ''}!\nNashriyot AI тизимига хуш келибсиз. Ролингиз: {ROLE_NAMES[user.role]}.\n\nБуйруқлар рўйхати: /help")

    async def cmd_help(self, s, user, args, raw, key) -> Reply:
        return Reply(HELP)

    async def cmd_task(self, s: AsyncSession, user: User, args, raw: str, key) -> Reply:
        if not raw:
            return Reply("Вазифа матнини ёзинг: /task Янги дарслик лойиҳасини оч, муҳаррирга бер...")
        due = tasks.parse_due(raw, self.tz)
        report = await self.coordinator.execute(s, raw, user=user, idempotency_key=key, due_at=due)
        if report.created:
            await audit.log_action(s, "task.created", user, "task", report.task.id, {"steps": [o.step.agent for o in report.outcomes]})
        text = report.summary
        if due and report.created:
            text += f"\n\nМуддат: {self._local(due)}"
        return Reply(text)

    async def cmd_today(self, s: AsyncSession, user, args, raw, key) -> Reply:
        local_now = datetime.now(self.tz)
        start = datetime.combine(local_now.date(), time(0), tzinfo=self.tz).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        end = start + timedelta(days=1)
        due_today = await tasks.due_between(s, start, end)
        overdue = await tasks.overdue_tasks(s)
        pending = await approvals.pending(s)
        created_today = await s.scalar(select(func.count()).select_from(Task).where(Task.created_at >= start, Task.parent_id.is_(None)))
        runs_today = await s.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.created_at >= start))
        lines = [f"📅 Бугун, {local_now:%d.%m.%Y}", f"Бугун яратилган вазифалар: {created_today}, агентлар ишлади: {runs_today} марта", ""]
        lines.append(f"⏳ Бугун муддати тугайдиган: {len(due_today)}")
        lines += [f"  • #{t.id} {t.title[:80]} — {self._local(t.due_at)}" for t in due_today[:10]]
        lines.append(f"⏰ Кечиккан: {len(overdue)}")
        lines += [f"  • #{t.id} {t.title[:80]} — {self._local(t.due_at)}" for t in overdue[:10]]
        lines.append(f"✋ Тасдиқ кутмоқда: {len(pending)}" + (" — /approve" if pending else ""))
        return Reply("\n".join(lines))

    async def cmd_books(self, s: AsyncSession, user: User, args: list[str], raw, key) -> Reply:
        sub = args[0].lower() if args else ""
        if sub == "new":
            title = " ".join(args[1:]).strip()
            if not title:
                raise ValueError("Китоб номини ёзинг: /books new «Номи»")
            kind = "textbook" if "дарслик" in title.lower() or "darslik" in title.lower() else "book"
            book = await books.create_book(s, title, kind=kind)
            await audit.log_action(s, "book.created", user, "book", book.id, {"title": book.title})
            return Reply(f"📗 Китоб қўшилди: #{book.id} «{book.title}» (қўлёзма)")
        if sub in {"stage", "stock", "text", "run", "price"}:
            if len(args) < 3:
                raise ValueError(f"Формат: /books {sub} <id> <қиймат>")
            book = await books.get_book(s, _int(args[1], "Китоб ID"))
            if book is None:
                raise LookupError("Китоб топилмади.")
            value = " ".join(args[2:])
            if sub == "stage":
                stage = STAGE_BY_NAME.get(value.lower())
                if stage is None:
                    raise ValueError("Босқичлар: " + ", ".join(STAGE_NAMES.values()))
                if stage == BookStage.PRINTING:
                    a, _ = await approvals.request_approval(
                        s, ApprovalKind.FINAL_PRINT, f"«{book.title}» китобини босмахонага юбориш", {"book_id": book.id},
                        requested_by=user, idempotency_key=f"{key}:print" if key else None,
                    )
                    return Reply(f"✋ Босмахонага юбориш раҳбар тасдиғини талаб қилади. Сўров #{a.id} яратилди.")
                await books.set_stage(s, book, stage)
                await audit.log_action(s, "book.stage", user, "book", book.id, {"stage": stage.value})
                return Reply(f"«{book.title}» → {STAGE_NAMES[stage]}")
            if sub == "stock":
                item = await books.add_stock(s, book.id, _int(value, "Сон"))
                await audit.log_action(s, "inventory.changed", user, "book", book.id, {"delta": value})
                return Reply(f"📦 «{book.title}» омборда: {item.quantity} дона")
            if sub == "text":
                book.manuscript_text = value
                return Reply(f"Қўлёзма матни сақланди ({len(value)} белги).")
            if sub == "run":
                book.print_run = _int(value, "Тираж")
                return Reply(f"Тираж: {book.print_run} нусха")
            if sub == "price":
                check(user, Role.DIRECTOR)
                book.price = _int(value, "Нарх")
                await audit.log_action(s, "book.price", user, "book", book.id, {"price": book.price})
                return Reply(f"Нарх: {fmt_money(book.price)}")
        rows = await books.list_books(s)
        if not rows:
            return Reply("Китоблар реестри бўш. Қўшиш: /books new «Номи»")
        lines = [f"📚 Китоблар ({len(rows)}):"]
        for b in rows:
            extra = []
            if b.print_run:
                extra.append(f"тираж {b.print_run}")
            if b.price:
                extra.append(fmt_money(b.price))
            lines.append(f"#{b.id} «{b.title}» — {BOOK_KIND.get(b.kind, b.kind)}, {STAGE_NAMES[b.stage]}" + (f" ({', '.join(extra)})" if extra else ""))
        return Reply("\n".join(lines))

    async def cmd_clients(self, s: AsyncSession, user: User, args: list[str], raw: str, key) -> Reply:
        if args and args[0].lower() == "new":
            body = raw.split(None, 1)[1] if len(raw.split(None, 1)) > 1 else ""
            name, _, phone = body.partition("|")
            if not name.strip():
                raise ValueError("Формат: /clients new Номи | телефон")
            c = await orders.create_client(s, name, phone.strip() or None)
            await audit.log_action(s, "client.created", user, "client", c.id)
            return Reply(f"👤 Мижоз қўшилди: #{c.id} {c.name}")
        rows = await orders.list_clients(s)
        if not rows:
            return Reply("Мижозлар йўқ. Қўшиш: /clients new Номи | телефон")
        return Reply("👥 Мижозлар:\n" + "\n".join(f"#{c.id} {c.name} {c.phone or ''}".rstrip() for c in rows))

    async def cmd_orders(self, s: AsyncSession, user: User, args: list[str], raw, key) -> Reply:
        sub = args[0].lower() if args else ""
        if sub == "new":
            if len(args) < 4:
                raise ValueError("Формат: /orders new <мижоз_id> <китоб_id> <сон> [нарх]")
            client_id, book_id, qty = _int(args[1], "Мижоз ID"), _int(args[2], "Китоб ID"), _int(args[3], "Сон")
            book = await books.get_book(s, book_id)
            if book is None or await s.get(orders.Client, client_id) is None:
                raise LookupError("Мижоз ёки китоб топилмади.")
            price = _int(args[4], "Нарх") if len(args) > 4 else book.price
            if price is None:
                raise ValueError("Китоб нархи белгиланмаган. Нархни ёзинг ёки раҳбар /books price орқали белгиласин.")
            o, created = await orders.create_order(s, client_id, book_id, qty, price, idempotency_key=key)
            if created:
                await audit.log_action(s, "order.created", user, "order", o.id, {"total": o.total})
            return Reply(f"🧾 Буюртма #{o.id}: {qty} × {fmt_money(price)} = {fmt_money(o.total)}" + ("" if created else " (аввал қайд этилган)"))
        if sub in {"pay", "discount"}:
            if len(args) < 3:
                raise ValueError(f"Формат: /orders {sub} <id> <қиймат>")
            o = await orders.get_order(s, _int(args[1], "Буюртма ID"))
            if o is None:
                raise LookupError("Буюртма топилмади.")
            if sub == "pay":
                _, created = await orders.record_payment(s, o, _int(args[2], "Сумма"), idempotency_key=key)
                if created:
                    await audit.log_action(s, "order.payment_received", user, "order", o.id, {"amount": args[2]})
                return Reply(f"💰 #{o.id}: тўланган {fmt_money(o.paid_amount)} / {fmt_money(o.total)} ({orders.PAYMENT_NAMES[o.payment_status]})")
            percent = float(args[2].replace("%", "").replace(",", "."))
            a, _ = await approvals.request_approval(
                s, ApprovalKind.DISCOUNT, f"#{o.id} буюртмага {percent}% чегирма ({o.client.name})",
                {"order_id": o.id, "percent": percent, "total_before": o.total}, requested_by=user, idempotency_key=key,
            )
            return Reply(f"✋ Чегирма раҳбар тасдиғидан кейин қўлланади. Сўров #{a.id}.")
        rows = await orders.list_orders(s)
        if not rows:
            return Reply("Буюртмалар йўқ. Қўшиш: /orders new <мижоз_id> <китоб_id> <сон> [нарх]")
        lines = ["🧾 Сўнгги буюртмалар:"]
        for o in rows:
            disc = f", чегирма {o.discount_percent:g}%" if o.discount_percent else ""
            lines.append(f"#{o.id} {o.client.name} — «{o.book.title}» × {o.quantity} = {fmt_money(o.total)}{disc}; {orders.PAYMENT_NAMES[o.payment_status]}")
        return Reply("\n".join(lines))

    async def cmd_finance(self, s: AsyncSession, user: User, args: list[str], raw, key) -> Reply:
        sub = args[0].lower() if args else "month"
        if sub in {"expense", "estimate"}:
            if len(args) < 3:
                raise ValueError(f"Формат: /finance {sub} <сумма> <тоифа> [#китоб] [изоҳ]")
            book_id = next((_int(a, "Китоб") for a in args[3:] if a.startswith("#")), None)
            note = " ".join(a for a in args[3:] if not a.startswith("#")) or None
            e, created = await finance.add_expense(s, _int(args[1], "Сумма"), args[2], book_id=book_id, description=note, is_estimate=sub == "estimate", idempotency_key=key)
            if created:
                await audit.log_action(s, "expense.created", user, "expense", e.id, {"amount": e.amount, "estimate": e.is_estimate})
            kind = "Тахминий харажат" if e.is_estimate else "Харажат"
            return Reply(f"💸 {kind} #{e.id}: {fmt_money(e.amount)} — {finance.CATEGORY_NAMES.get(e.category, e.category)}. Молиявий ҳисобот янгиланди.")
        if sub == "revenue":
            if len(args) < 2:
                raise ValueError("Формат: /finance revenue <сумма> [изоҳ]")
            r, created = await finance.add_revenue(s, _int(args[1], "Сумма"), description=" ".join(args[2:]) or None, idempotency_key=key)
            if created:
                await audit.log_action(s, "revenue.created", user, "revenue", r.id, {"amount": r.amount})
            return Reply(f"💰 Тушум #{r.id}: {fmt_money(r.amount)}")
        if sub == "cost":
            if len(args) < 2:
                raise ValueError("Формат: /finance cost <китоб_id>")
            result, _ = await self.coordinator.agents["finance"].run(_ctx(s, user, f"#{_int(args[1], 'Китоб ID')} таннарх"))
            return Reply(result.summary if result else "Ҳисоблашда хатолик.")
        if sub == "payment":
            if len(args) < 3:
                raise ValueError("Формат: /finance payment <сумма> <кимга/нима учун>")
            amount = _int(args[1], "Сумма")
            category = finance.categorize(" ".join(args[2:]))
            a, _ = await approvals.request_approval(
                s, ApprovalKind.PAYMENT, f"Тўлов: {fmt_money(amount)} — {' '.join(args[2:])[:120]}",
                {"amount": amount, "category": category, "recipient": " ".join(args[2:])[:200]}, requested_by=user, idempotency_key=key,
            )
            return Reply(f"✋ Тўлов сўрови #{a.id} раҳбарга юборилди. Тизим ўзи пул ўтказмайди.")
        period = reporting.PERIOD_ALIASES.get(sub, "month")
        start, end = reporting.period_bounds(period, self.tz)
        r = await finance.period_report(s, start, end + timedelta(seconds=1))
        return Reply(reporting.format_finance(r, self.tz, f"Молиявий ҳисобот ({reporting.PERIODS[period]})"))

    async def cmd_report(self, s: AsyncSession, user, args: list[str], raw, key) -> Reply:
        period = reporting.PERIOD_ALIASES.get(args[0].lower(), "day") if args else "day"
        k = await reporting.build_kpi(s, period, self.tz)
        return Reply(reporting.format_kpi(k, self.tz))

    async def cmd_approve(self, s: AsyncSession, user: User, args: list[str], raw, key) -> Reply:
        if len(args) >= 2:
            approve = args[1].lower() in {"ha", "ҳа", "ха", "yes", "ok", "тасдиқ", "tasdiq", "+"}
            reject = args[1].lower() in {"yo'q", "йўқ", "no", "rad", "рад", "-"}
            if not (approve or reject):
                raise ValueError("Формат: /approve <id> ҳа|йўқ")
            telegram_id, approval_id = user.telegram_id, _int(args[0], "ID")
            await s.rollback()
            return await self.decide(telegram_id, approval_id, approve)
        rows = await approvals.pending(s)
        if not rows:
            return Reply("✅ Тасдиқ кутаётган қарорлар йўқ.")
        lines, buttons = [f"✋ Тасдиқ кутмоқда ({len(rows)}):"], []
        for a in rows:
            who = AGENT_TITLES.get(a.requested_by_agent or "", "ходим")
            lines.append(f"#{a.id} [{KIND_NAMES[a.kind]}] {a.title} — сўради: {who}, {self._local(a.created_at)}")
            buttons.append([(f"✅ #{a.id}", f"ap:{a.id}:1"), (f"🚫 #{a.id}", f"ap:{a.id}:0")])
        return Reply("\n".join(lines), buttons)

    # ------------------------------------------------------------------ sales agent leads
    async def cmd_leads(self, s: AsyncSession, user, args, raw, key) -> Reply:
        from app.salesbot.agent import CHANNEL_NAMES
        from app.salesbot.models import Lead, LeadStage

        stage_names = {LeadStage.NEW: "янги", LeadStage.NEED: "эҳтиёж", LeadStage.QUANTITY: "миқдор", LeadStage.NAME: "исм",
                       LeadStage.PHONE: "телефон", LeadStage.ADDRESS: "манзил", LeadStage.CONFIRM: "тасдиқ", LeadStage.WON: "✅ сотилди",
                       LeadStage.HANDOFF: "🙋 менежерда", LeadStage.LOST: "йўқотилди"}
        rows = list(await s.scalars(select(Lead).order_by(Lead.last_message_at.desc()).limit(20)))
        if not rows:
            return Reply("Лидлар йўқ. Мижозлар Instagram, WhatsApp, Telegram ёки телефон орқали ёзса, шу ерда кўринади.")
        total = await s.scalar(select(func.count(Lead.id)))
        won = await s.scalar(select(func.count(Lead.id)).where(Lead.stage == LeadStage.WON))
        lines = [f"🧲 Лидлар (охирги 20). Жами: {total}, сотилган: {won} ({round(100 * won / total) if total else 0}%)", ""]
        for x in rows:
            deal = f" · {x.product_name} × {x.quantity}" if x.product_name and x.quantity else (f" · {x.product_name}" if x.product_name else "")
            lines.append(f"#{x.id} {CHANNEL_NAMES.get(x.channel, x.channel)} · {x.name or x.display_name or x.external_id} · {stage_names[x.stage]}{deal}")
        lines.append("\nЁзишма: /lead <id> · Ботга қайтариш: /lead bot <id>")
        return Reply("\n".join(lines))

    async def cmd_lead(self, s: AsyncSession, user, args, raw, key) -> Reply:
        from app.salesbot.models import Lead, LeadMessage, LeadStage

        if args and args[0].lower() == "bot":
            check(user, Role.STAFF)
            lead = await s.get(Lead, _int(args[1] if len(args) > 1 else "", "лид ID"))
            if lead is None:
                raise LookupError("Лид топилмади.")
            lead.stage = LeadStage.NEED if not lead.product_id else LeadStage.QUANTITY if not lead.quantity else LeadStage.NAME
            await audit.log_action(s, "lead.back_to_bot", user, "lead", lead.id)
            return Reply(f"🤖 Лид #{lead.id} яна ботга қайтарилди.")
        if not args:
            return Reply("Фойдаланиш: /lead <id>")
        lead = await s.get(Lead, _int(args[0], "лид ID"))
        if lead is None:
            raise LookupError("Лид топилмади.")
        msgs = list(await s.scalars(select(LeadMessage).where(LeadMessage.lead_id == lead.id).order_by(LeadMessage.id.desc()).limit(30)))[::-1]
        lines = [f"Лид #{lead.id} · {lead.channel} · {lead.stage.value} · тил: {lead.language}",
                 f"{lead.name or lead.display_name or '—'} · {lead.phone or '—'} · {lead.address or '—'}"]
        if lead.product_name:
            lines.append(f"{lead.product_name} × {lead.quantity or '?'}" + (f" = {fmt_money(lead.total)} сўм" if lead.total else ""))
        lines.append("")
        for m in msgs:
            lines.append(("👤 " if m.direction == "in" else "🤖 ") + m.text)
        return Reply("\n".join(lines))

    async def cmd_reply(self, s: AsyncSession, user, args, raw, key) -> Reply:
        sales = getattr(self, "sales", None)
        if sales is None:
            return Reply("Сотув агенти ўчирилган.")
        lead_id_text, _, text = raw.strip().partition(" ")
        if not lead_id_text or not text.strip():
            return Reply("Фойдаланиш: /reply <лид_id> <мижозга матн>\nМасалан: /reply 12 Pasportingiz tekshirildi, taqiq yo'q ✅")
        ok, info = await sales.manager_reply(_int(lead_id_text, "Лид ID"), text)
        if ok:
            await audit.log_action(s, "lead.manager_reply", user, "lead", _int(lead_id_text, "Лид ID"))
        return Reply(info)

    async def cmd_answer(self, s: AsyncSession, user, args, raw, key) -> Reply:
        sales = getattr(self, "sales", None)
        if sales is None:
            return Reply("Сотув агенти ўчирилган.")
        lead_id_text, _, text = raw.strip().partition(" ")
        if not lead_id_text or not text.strip():
            return Reply("Фойдаланиш: /answer <лид_id> <жавоб>\nЖавоб мижозга боради ва базага қўшилади: кейинги сафар агент ўзи жавоб беради.")
        ok, info = await sales.manager_answer(_int(lead_id_text, "Лид ID"), text)
        if ok:
            await audit.log_action(s, "lead.answer", user, "lead", _int(lead_id_text, "Лид ID"))
        return Reply(info)

    async def cmd_result(self, s: AsyncSession, user, args, raw, key) -> Reply:
        sales = getattr(self, "sales", None)
        if sales is None:
            return Reply("Сотув агенти ўчирилган.")
        if len(args) < 2:
            return Reply("Фойдаланиш: /result <лид_id> ban|ok [изоҳ]\n"
                         "ban — тақиқ бор: мижозга натижа ва депорт хизмати таклифи боради\n"
                         "ok — тақиқ йўқ: натижа ва иш ҳужжатлари таклифи")
        note = raw.strip().split(None, 2)[2] if len(raw.strip().split(None, 2)) > 2 else ""
        lead_id = _int(args[0], "Лид ID")
        ok, info = await sales.send_result(lead_id, args[1], note)
        if ok:
            await audit.log_action(s, "lead.result", user, "lead", lead_id, {"outcome": args[1]})
        return Reply(info)

    async def cmd_weekly(self, s: AsyncSession, user, args, raw, key) -> Reply:
        sales = getattr(self, "sales", None)
        if sales is None:
            return Reply("Сотув агенти ўчирилган.")
        return Reply(await sales.weekly_report())

    async def cmd_status(self, s: AsyncSession, user, args, raw, key) -> Reply:
        sales = getattr(self, "sales", None)
        if sales is None:
            return Reply("Сотув агенти ўчирилган.")
        if len(args) < 2:
            return Reply("Фойдаланиш: /status <лид_id> <1-5> [изоҳ]\n1) Ҳужжатлар кутилмоқда 2) Ҳужжатлар қабул қилинди 3) Ариза топширилди "
                         "4) Натижа кутилмоқда 5) Якунланди — мижозга хабар боради, 5 да баҳо сўралади")
        parts = raw.strip().split(None, 2)
        lead_id = _int(args[0], "Лид ID")
        ok, info = await sales.set_status(lead_id, _int(args[1], "Ҳолат") - 1, parts[2] if len(parts) > 2 else "")
        if ok:
            await audit.log_action(s, "lead.status", user, "lead", lead_id, {"step": args[1]})
        return Reply(info)

    async def cmd_faq(self, s: AsyncSession, user, args, raw, key) -> Reply:
        from app.salesbot.models import FaqEntry

        if args and args[0].lower() == "del":
            check(user, Role.STAFF)
            row = await s.get(FaqEntry, _int(args[1] if len(args) > 1 else "", "FAQ ID"))
            if row is None:
                raise LookupError("Бундай жавоб йўқ.")
            row.active = False
            await audit.log_action(s, "faq.disabled", user, "faq", row.id)
            return Reply(f"#{row.id} жавоб ўчирилди: агент энди уни ишлатмайди.")
        rows = list(await s.scalars(select(FaqEntry).where(FaqEntry.active.is_(True)).order_by(FaqEntry.id.desc()).limit(30)))
        if not rows:
            return Reply("Менежерлар ҳали /answer билан жавоб бермаган. Асосий жавоблар sales_faq.json файлида.")
        lines = ["📚 Агент ўрганган жавоблар (охирги 30):"]
        for r in rows:
            lines.append(f"#{r.id} [{r.language}] «{r.question[:80]}» → {r.answer[:120]} · {r.uses} марта")
        lines.append("\nЎчириш: /faq del <id>")
        return Reply("\n".join(lines))

    async def cmd_paid(self, s: AsyncSession, user, args, raw, key) -> Reply:
        sales = getattr(self, "sales", None)
        if sales is None:
            return Reply("Сотув агенти ўчирилган.")
        if not args:
            return Reply("Фойдаланиш: /paid <лид_id> — олдиндан тўлов келганини белгилайди ва мижозга хабар беради.")
        lead_id = _int(args[0], "Лид ID")
        ok, info = await sales.mark_paid(lead_id)
        if ok:
            await audit.log_action(s, "lead.paid", user, "lead", lead_id)
        return Reply(info)

    async def cmd_files(self, s: AsyncSession, user, args, raw, key) -> Reply:
        from app.salesbot.models import LeadFile

        if not args:
            return Reply("Фойдаланиш: /files <лид_id>")
        lead_id = _int(args[0], "Лид ID")
        rows = list(await s.scalars(select(LeadFile).where(LeadFile.lead_id == lead_id).order_by(LeadFile.id)))
        if not rows:
            return Reply(f"Лид #{lead_id} ҳали файл юбормаган.")
        kinds = {"document": "📎", "photo": "🖼", "audio": "🎤"}
        lines = [f"Лид #{lead_id} файллари ({len(rows)} та):"]
        for f in rows:
            line = f"{kinds.get(f.kind, '📎')} {f.file_name} · {round(f.size / 1024)} KB · {self._local(f.created_at)}"
            if f.transcript:
                line += f"\n   «{f.transcript[:200]}»"
            if not f.path:
                line += " · (юклаб олинмаган)"
            lines.append(line)
        lines.append("\nФайллар келган заҳоти сизга юборилган. Серверда: " + (str(rows[0].path).rsplit("/", 1)[0] if rows[0].path else "—"))
        return Reply("\n".join(lines))

    async def cmd_agents(self, s: AsyncSession, user, args, raw, key) -> Reply:
        stats = {a.agent: a for a in await reporting.agent_stats(s)}
        enabled = await settings_store.automation_enabled(s)
        lines = ["🤖 Агентлар ҳолати", f"Автоматика: {'ёқилган ▶️' if enabled else 'тўхтатилган ⏸'}", f"AI режими: {'OpenAI' if self.coordinator.llm.enabled else 'demo (қоидалар асосида)'}", ""]
        for name, agent in self.coordinator.agents.items():
            st = stats.get(name)
            if st:
                lines.append(f"• {agent.title}: {st.runs} марта, хато {st.errors}, ўртача {st.avg_ms:.0f} мс, охирги {self._local(st.last_run)}")
            else:
                lines.append(f"• {agent.title}: ҳали ишламаган")
        return Reply("\n".join(lines))

    # ------------------------------------------------------------------ agentic engine
    def _workflow_text(self, wf) -> str:
        from app.agentic.models import WorkflowStatus

        head = {WorkflowStatus.DONE: "✅ Бажарилди", WorkflowStatus.WAITING_APPROVAL: "✋ Раҳбар тасдиғини кутмоқда",
                WorkflowStatus.FAILED: "❌ Тўхтади", WorkflowStatus.RUNNING: "⏳ Бажарилмоқда"}[wf.status]
        body = wf.result if wf.status == WorkflowStatus.DONE else (wf.error or "")
        if wf.status == WorkflowStatus.WAITING_APPROVAL:
            body = f"Тасдиқ сўрови #{wf.waiting_approval_id} — /approve. Тасдиқлангандан кейин иш ўзи давом этади."
        return f"🤖 Жараён #{wf.id}: {head}\n{body}".strip() + f"\n(қадамлар: /flow {wf.id})"

    async def cmd_ai(self, s: AsyncSession, user: User, args, raw: str, key) -> Reply:
        if self.runtime is None:
            return Reply("Агентли режим уланмаган.")
        if not raw:
            return Reply("Мақсадни ёзинг: /ai #1 китоб таннархини ҳисобла ва босмахонага юбор")
        wf, created = await self.runtime.start(s, raw, user, idempotency_key=key)
        if not created:
            return Reply(f"Бу буйруқ аллақачон қабул қилинган: жараён #{wf.id}.")
        return Reply(self._workflow_text(wf))

    async def cmd_flow(self, s: AsyncSession, user: User, args: list[str], raw, key) -> Reply:
        from app.agentic.models import Workflow
        from app.agentic.runtime import workflow_steps

        if not args:
            rows = list(await s.scalars(select(Workflow).order_by(Workflow.id.desc()).limit(10)))
            if not rows:
                return Reply("Ҳали агентли жараёнлар йўқ. Бошлаш: /ai <мақсад>")
            return Reply("🤖 Сўнгги жараёнлар:\n" + "\n".join(f"#{w.id} [{w.status.value}] {w.goal[:70]}" for w in rows))
        wf = await s.get(Workflow, _int(args[0], "Жараён ID"))
        if wf is None:
            raise LookupError("Жараён топилмади.")
        icons = {"ok": "✓", "error": "✕", "rejected": "⛔", "waiting": "✋", "resumed": "↻"}
        lines = [f"🤖 Жараён #{wf.id}: {wf.goal[:120]}", f"Ҳолат: {wf.status.value} · модель чақирувлари: {wf.model_calls} · токенлар: {wf.tokens_in}+{wf.tokens_out}", ""]
        for st in await workflow_steps(s, wf.id):
            summary = (st.result or {}).get("summary") or (st.result or {}).get("error") or ""
            lines.append(f"{icons.get(st.status, '•')} {st.kind}:{st.name} {summary}"[:200])
        return Reply("\n".join(lines))

    async def cmd_pause(self, s: AsyncSession, user: User, args, raw, key) -> Reply:
        await settings_store.set_setting(s, settings_store.AUTOMATION_ENABLED, False)
        await audit.log_action(s, "automation.paused", user)
        return Reply("⏸ Автоматик жараёнлар (эслатмалар, кечикиш хабарлари, кунлик ҳисобот) тўхтатилди. Қайта ёқиш: /resume")

    async def cmd_resume(self, s: AsyncSession, user: User, args, raw, key) -> Reply:
        await settings_store.set_setting(s, settings_store.AUTOMATION_ENABLED, True)
        await audit.log_action(s, "automation.resumed", user)
        return Reply("▶️ Автоматик жараёнлар қайта ёқилди.")


def _ctx(session, user, instruction):
    from app.agents.base import AgentContext

    return AgentContext(session=session, instruction=instruction, user=user)

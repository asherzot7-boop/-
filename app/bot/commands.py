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
from app.database.models import AgentRun, ApprovalKind, BookStage, Role, Task, User, utcnow
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
/pause · /resume — автоматик жараёнлар (раҳбар)

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
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], coordinator: Coordinator, settings: Settings) -> None:
        self.sf = session_factory
        self.coordinator = coordinator
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
                command, args_text = "task", text
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
            return Reply(f"{mark}: #{a.id} {a.title}\n{a.execution_result or ''}")

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

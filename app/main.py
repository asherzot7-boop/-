"""Single entry point.

    python -m app.main            # migrate, then run bot (or demo mode) + API + automation
    python -m app.main demo       # interactive console that behaves like the bot
    python -m app.main migrate    # only apply database migrations
    python -m app.main seed       # add sample data for a demo
    python -m app.main sales      # chat with the sales agent as a client
    python -m app.main backup     # zip the database and client documents (--to <folder>)
    python -m app.main eval       # check the sales agent on sales_eval_cases.json
    python -m app.main visual     # rebuild visual/journeys*.html from three real demo conversations
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.agents.coordinator import Coordinator
from app.agents.llm import build_llm
from app.bot.commands import CommandService
from app.config import Settings, get_settings
from app.database.session import make_engine, make_session_factory, run_migrations
from app.logging_setup import setup_logging
from app.security.permissions import sync_users
from app.services import reporting

log = logging.getLogger("app")

DEMO_DIRECTOR_ID = 1


@dataclass
class Container:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    coordinator: Coordinator
    service: CommandService
    sales: object | None = None


async def build_container(settings: Settings) -> Container:
    reporting.register_event_handlers()
    engine = make_engine(settings.database_url)
    sf = make_session_factory(engine)
    directors = settings.director_ids
    if settings.demo_mode and not directors:
        directors = [DEMO_DIRECTOR_ID]
        log.warning("DIRECTOR_IDS берилмаган: demo режимда раҳбар ID=%s", DEMO_DIRECTOR_ID)
    async with sf() as session:
        await sync_users(session, directors, settings.staff_ids, settings.viewer_ids)
    coordinator = Coordinator(build_llm(settings))
    sales = None
    if settings.sales_enabled:
        from app.salesbot.agent import SalesAgent, build_sales_model
        from app.salesbot.channels.meta import MetaSender
        from app.salesbot.media import Transcriber
        from app.salesbot.outbound import MetaChannelSender, Outbound

        outbound = Outbound()
        outbound.register(("instagram", "whatsapp", "messenger"), MetaChannelSender(MetaSender(settings)))
        from app.salesbot.outbound import WebSender

        outbound.register(("web",), WebSender())
        key = settings.openai_api_key.get_secret_value() if settings.llm_enabled else None
        from app.salesbot.tts import build_tts

        sales = SalesAgent(sf, settings, model=build_sales_model(settings), outbound=outbound,
                           transcriber=Transcriber(key, settings.sales_stt_model), tts=build_tts(settings))
    service = CommandService(sf, coordinator, settings, runtime=build_runtime(settings))
    service.sales = sales
    return Container(settings, engine, sf, coordinator, service, sales)


def build_runtime(settings: Settings):
    from app.agentic.llm import OpenAIToolModel, RuleToolModel
    from app.agentic.runtime import AgentRuntime

    if settings.llm_enabled:
        model = OpenAIToolModel(settings.openai_api_key.get_secret_value(), settings.openai_model, settings.openai_timeout)
    else:
        model = RuleToolModel()
    return AgentRuntime(model)


async def _serve_api(c: Container) -> None:
    import uvicorn

    from app.api.routes import create_api

    config = uvicorn.Config(create_api(c.settings, c.session_factory, sales=c.sales), host=c.settings.api_host, port=c.settings.api_port, log_level="warning")
    await uvicorn.Server(config).serve()


async def sales_reminder_loop(c: Container) -> None:
    """Every few minutes: one reminder to clients who stopped halfway (skipped while automation is paused)."""
    from app.services import settings_store

    while True:
        try:
            async with c.session_factory() as s:
                enabled = await settings_store.automation_enabled(s)
            if enabled:
                n = await c.sales.remind_stale()
                if n:
                    log.info("Сотув эслатмалари юборилди: %s", n)
            if enabled and c.settings.sales_reengage_enabled:
                n = await c.sales.reengage()
                if n:
                    log.info("Эски мижозларга хабар: %s", n)
            if enabled:
                await c.sales.booking_reminders()
                n = await c.sales.sla_check()
                if n:
                    log.info("Жавобсиз қолган мурожаатлар раҳбарга юборилди: %s", n)
            if enabled and await c.sales.weekly_report_due() and c.sales.notifier is not None:
                from app.notify import director_ids

                text = await c.sales.weekly_report()
                async with c.session_factory() as s:
                    for tid in await director_ids(s):
                        await c.sales.notifier.send(tid, text)
            # privacy: delete expired client files even while automation is paused
            n = await c.sales.purge_files()
            if n:
                log.info("Муддати ўтган мижоз файллари ўчирилди: %s", n)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Сотув эслатмаларида хатолик")
        await asyncio.sleep(c.settings.sales_reminder_check_seconds)


def _make_bot(token: str, what: str):
    """aiogram.Bot, or None when the token is malformed (the API and the setup page must still start)."""
    from aiogram import Bot
    from aiogram.utils.token import TokenValidationError

    try:
        return Bot(token)
    except TokenValidationError:
        log.error("%s: токен нотўғри ёзилган. /setup саҳифасида тузатинг", what)
        return None


async def _keep_polling(what: str, start, retry: float = 30.0) -> None:
    """Run a Telegram bot; with a wrong token or no internet log it and try again later instead of
    taking the whole agent (and the panel where the token is fixed) down with it."""
    while True:
        try:
            await start()
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - any failure here (bad token, no internet) must not stop the API
            log.error("%s: Telegram'га уланиб бўлмади (%s). %.0f сониядан кейин қайта уринилади", what, e, retry)
            await asyncio.sleep(retry)


async def run(settings: Settings) -> None:
    from app.notify import ConsoleNotifier, TelegramNotifier
    from app.services.automation import scheduler_loop

    c = await build_container(settings)
    jobs: list[asyncio.Task] = []
    sales_bot = None
    if c.sales is not None and settings.sales_bot_token is not None and settings.sales_bot_token.get_secret_value():
        from app.salesbot.channels.telegram import build_sales_dispatcher
        from app.salesbot.outbound import TelegramSender

        sales_bot = _make_bot(settings.sales_bot_token.get_secret_value(), "Мижозлар боти")
    if sales_bot is not None:
        c.sales.outbound.register(("telegram", "telegram_business"), TelegramSender(sales_bot))
        sales_dp = build_sales_dispatcher(c.sales)
        jobs.append(asyncio.create_task(
            _keep_polling("Мижозлар боти", lambda: sales_dp.start_polling(sales_bot, handle_signals=False)), name="sales-bot"))
        log.info("Мижозлар учун сотув боти ишга тушди")
    if c.sales is not None:
        jobs.append(asyncio.create_task(sales_reminder_loop(c), name="sales-reminders"))
    if settings.api_enabled:
        jobs.append(asyncio.create_task(_serve_api(c), name="api"))
        log.info("API: http://%s:%s/health", settings.api_host, settings.api_port)

    bot = None if settings.demo_mode else _make_bot(settings.bot_token.get_secret_value(), "Ходимлар боти")
    if bot is None:
        if settings.demo_mode:
            log.warning("BOT_TOKEN берилмаган — DEMO режим. Telegram уланмайди. Консолда синаш: python -m app.main demo")
        if c.sales is not None:
            c.sales.notifier = TelegramNotifier(sales_bot) if sales_bot else ConsoleNotifier()
        jobs.append(asyncio.create_task(scheduler_loop(c.session_factory, ConsoleNotifier(), settings), name="automation"))
        await asyncio.gather(*jobs)
        return

    from app.bot.setup import build_dispatcher, set_commands

    dp = build_dispatcher(c.service, c.session_factory)
    if c.sales is not None:
        c.sales.notifier = TelegramNotifier(bot)  # managers get orders in the staff bot
    jobs.append(asyncio.create_task(scheduler_loop(c.session_factory, TelegramNotifier(bot), settings), name="automation"))
    async def staff_bot() -> None:
        await set_commands(bot)
        log.info("Telegram бот ишга тушди")
        await dp.start_polling(bot, handle_signals=False)

    try:
        await _keep_polling("Ходимлар боти", staff_bot)
    finally:
        for j in jobs:
            j.cancel()
        with contextlib.suppress(Exception):
            await bot.session.close()
        if sales_bot is not None:
            with contextlib.suppress(Exception):
                await sales_bot.session.close()
        await c.engine.dispose()


async def demo_console(settings: Settings, telegram_id: int) -> None:
    c = await build_container(settings)
    print(f"Nashriyot AI demo консоли. Фойдаланувчи Telegram ID={telegram_id}. Чиқиш: exit")
    print("Буйруқлар: /help. Тасдиқлаш тугмаси ўрнига: /approve <id> ҳа")
    n = 0
    loop = asyncio.get_running_loop()
    while True:
        try:
            line = await loop.run_in_executor(None, input, "> ")
        except (EOFError, KeyboardInterrupt):
            break
        if line.strip() in {"exit", "quit", "чиқиш"}:
            break
        n += 1
        reply = await c.service.dispatch(telegram_id, line, idempotency_key=f"console:{telegram_id}:{n}:{line}")
        print(reply.text)
        for row in reply.buttons:
            print("   " + "   ".join(f"[{t} → {d}]" for t, d in row))
    await c.engine.dispose()


async def sales_console(settings: Settings, channel: str, client_id: str) -> None:
    """Talk to the sales agent as a client would (no Instagram/Telegram needed)."""
    from app.notify import ConsoleNotifier

    c = await build_container(settings)
    if c.sales is None:
        print("SALES_ENABLED=false")
        return
    c.sales.notifier = ConsoleNotifier()
    print(f"Сотув агенти: мижоз сифатида ёзинг (канал={channel}). Чиқиш: exit")
    first = await c.sales.handle(channel, client_id, "")
    print(first.text)
    loop = asyncio.get_running_loop()
    while True:
        try:
            line = await loop.run_in_executor(None, input, "мижоз> ")
        except (EOFError, KeyboardInterrupt):
            break
        if line.strip() in {"exit", "quit", "чиқиш"}:
            break
        reply = await c.sales.handle(channel, client_id, line)
        print(reply.text or "(жим — менежер жавоб беради)")
        for tid, text in c.sales.notifier.sent:
            print(f"   [менежерга → {tid}] " + text.replace("\n", "\n   "))
        c.sales.notifier.sent.clear()
    await c.engine.dispose()


async def seed(settings: Settings) -> None:
    from app.services import books, finance, orders

    c = await build_container(settings)
    async with c.session_factory() as s:
        b1 = await books.create_book(s, "Она тили 5-синф", kind="textbook", target_audience="5-синф", print_run=2000, price=45000)
        b2 = await books.create_book(s, "Наманган эртаклари", kind="book", print_run=1000, price=60000)
        await books.add_stock(s, b2.id, 350)
        cl1 = await orders.create_client(s, "12-мактаб", "+998 90 000 00 01", "school")
        cl2 = await orders.create_client(s, "«Китоб олами» дўкони", "+998 90 000 00 02", "shop")
        o1, _ = await orders.create_order(s, cl1.id, b1.id, 120, 45000)
        o2, _ = await orders.create_order(s, cl2.id, b2.id, 50, 60000)
        await orders.record_payment(s, o1, 3_000_000)
        await orders.record_payment(s, o2, 3_000_000)
        await finance.add_expense(s, 18_000_000, "printing", book_id=b1.id, description="босмахона")
        await finance.add_expense(s, 4_000_000, "royalty", book_id=b1.id, is_estimate=True, description="гонорар (тахминий)")
        await finance.add_expense(s, 3_500_000, "rent", description="октябрь ижараси")
        await s.commit()
    await c.engine.dispose()
    print("Намуна маълумотлар қўшилди (2 китоб, 2 мижоз, 2 буюртма, 3 харажат).")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.main")
    parser.add_argument("mode", nargs="?", default="run", choices=["run", "demo", "migrate", "seed", "sales", "backup", "eval", "visual"])
    parser.add_argument("--as-id", type=int, default=None, help="demo: Telegram ID (default: first director)")
    parser.add_argument("--to", default=None, help="backup: destination folder (default BACKUP_DIR)")
    parser.add_argument("--cases", default="sales_eval_cases.json", help="eval: conversations to check")
    parser.add_argument("--channel", default="console", help="sales: channel name to simulate (instagram, whatsapp, telegram, phone)")
    a = parser.parse_args(argv)
    if a.mode == "visual":  # rebuild visual/journeys*.html from the real agent; needs no .env, keys or database
        from app.salesbot.journeys import build

        for path in asyncio.run(build()):
            print(path)
        print("Очинг: http://127.0.0.1:8000/visual (бот ишлаётганда) ёки visual/index.html")
        return

    settings = get_settings()
    setup_logging(settings.log_level, settings.secret_values())
    run_migrations(settings.database_url)  # before the event loop: Alembic env uses asyncio.run
    if a.mode == "migrate":
        log.info("Миграциялар қўлланди")
    elif a.mode == "demo":
        asyncio.run(demo_console(settings, a.as_id or (settings.director_ids[0] if settings.director_ids else DEMO_DIRECTOR_ID)))
    elif a.mode == "eval":
        from app.salesbot.agent import build_sales_model
        from app.salesbot.evaluation import report
        from app.salesbot.evaluation import run as run_eval

        results = asyncio.run(run_eval(settings, a.cases, model=build_sales_model(settings)))
        print(report(results))
        raise SystemExit(0 if all(r.ok for r in results) else 1)
    elif a.mode == "backup":
        from app.backup import make_backup

        print(make_backup(settings, a.to))
    elif a.mode == "sales":
        asyncio.run(sales_console(settings, a.channel, "console-client"))
    elif a.mode == "seed":
        asyncio.run(seed(settings))
    else:
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(run(settings))


if __name__ == "__main__":
    main()

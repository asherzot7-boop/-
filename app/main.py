"""Single entry point.

    python -m app.main            # migrate, then run bot (or demo mode) + API + automation
    python -m app.main demo       # interactive console that behaves like the bot
    python -m app.main migrate    # only apply database migrations
    python -m app.main seed       # add sample data for a demo
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
    return Container(settings, engine, sf, coordinator, CommandService(sf, coordinator, settings))


async def _serve_api(c: Container) -> None:
    import uvicorn

    from app.api.routes import create_api

    config = uvicorn.Config(create_api(c.settings, c.session_factory), host=c.settings.api_host, port=c.settings.api_port, log_level="warning")
    await uvicorn.Server(config).serve()


async def run(settings: Settings) -> None:
    from app.notify import ConsoleNotifier, TelegramNotifier
    from app.services.automation import scheduler_loop

    c = await build_container(settings)
    jobs: list[asyncio.Task] = []
    if settings.api_enabled:
        jobs.append(asyncio.create_task(_serve_api(c), name="api"))
        log.info("API: http://%s:%s/health", settings.api_host, settings.api_port)

    if settings.demo_mode:
        log.warning("BOT_TOKEN берилмаган — DEMO режим. Telegram уланмайди. Консолда синаш: python -m app.main demo")
        jobs.append(asyncio.create_task(scheduler_loop(c.session_factory, ConsoleNotifier(), settings), name="automation"))
        await asyncio.gather(*jobs)
        return

    from aiogram import Bot

    from app.bot.setup import build_dispatcher, set_commands

    bot = Bot(settings.bot_token.get_secret_value())
    dp = build_dispatcher(c.service, c.session_factory)
    jobs.append(asyncio.create_task(scheduler_loop(c.session_factory, TelegramNotifier(bot), settings), name="automation"))
    try:
        await set_commands(bot)
        log.info("Telegram бот ишга тушди")
        await dp.start_polling(bot, handle_signals=False)
    finally:
        for j in jobs:
            j.cancel()
        with contextlib.suppress(Exception):
            await bot.session.close()
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
    parser.add_argument("mode", nargs="?", default="run", choices=["run", "demo", "migrate", "seed"])
    parser.add_argument("--as-id", type=int, default=None, help="demo: Telegram ID (default: first director)")
    a = parser.parse_args(argv)

    settings = get_settings()
    setup_logging(settings.log_level, settings.secret_values())
    run_migrations(settings.database_url)  # before the event loop: Alembic env uses asyncio.run
    if a.mode == "migrate":
        log.info("Миграциялар қўлланди")
    elif a.mode == "demo":
        asyncio.run(demo_console(settings, a.as_id or (settings.director_ids[0] if settings.director_ids else DEMO_DIRECTOR_ID)))
    elif a.mode == "seed":
        asyncio.run(seed(settings))
    else:
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(run(settings))


if __name__ == "__main__":
    main()

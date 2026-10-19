"""Role based access: who may run which command."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Role, User

ROLE_LEVEL = {Role.VIEWER: 1, Role.STAFF: 2, Role.DIRECTOR: 3}

ROLE_NAMES = {Role.DIRECTOR: "раҳбар", Role.STAFF: "ходим", Role.VIEWER: "кузатувчи"}

# Minimum role per command. Commands not listed require STAFF.
COMMAND_ROLE: dict[str, Role] = {
    "start": Role.VIEWER,
    "help": Role.VIEWER,
    "today": Role.VIEWER,
    "books": Role.VIEWER,
    "orders": Role.VIEWER,
    "clients": Role.VIEWER,
    "finance": Role.VIEWER,
    "report": Role.VIEWER,
    "agents": Role.VIEWER,
    "task": Role.STAFF,
    "ai": Role.STAFF,
    "flow": Role.VIEWER,
    "approve": Role.DIRECTOR,
    "pause": Role.DIRECTOR,
    "resume": Role.DIRECTOR,
}

# Sub-commands that change data need STAFF even under a VIEWER command.
WRITE_SUBCOMMANDS = {"new", "add", "stage", "pay", "expense", "estimate", "revenue", "discount", "status", "stock", "text", "run", "price", "message", "payment", "contract"}


class AccessDenied(Exception):
    pass


def has_role(user: User, required: Role) -> bool:
    return user.is_active and ROLE_LEVEL[user.role] >= ROLE_LEVEL[required]


def required_role(command: str, args: list[str]) -> Role:
    role = COMMAND_ROLE.get(command, Role.STAFF)
    if role == Role.VIEWER and args and args[0].lower() in WRITE_SUBCOMMANDS:
        return Role.STAFF
    return role


def check(user: User, required: Role) -> None:
    if not has_role(user, required):
        raise AccessDenied(f"Бу амал учун «{ROLE_NAMES[required]}» ҳуқуқи керак.")


async def get_active_user(session: AsyncSession, telegram_id: int) -> User | None:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None or not user.is_active:
        return None
    return user


async def sync_users(session: AsyncSession, directors: list[int], staff: list[int], viewers: list[int]) -> None:
    """Make the users table match the IDs configured in .env.

    A higher role wins if an ID is listed twice. Users removed from .env are
    deactivated, not deleted, so their history stays linked.
    """
    wanted: dict[int, Role] = {}
    for ids, role in ((viewers, Role.VIEWER), (staff, Role.STAFF), (directors, Role.DIRECTOR)):
        for tid in ids:
            wanted[tid] = role
    existing = {u.telegram_id: u for u in (await session.scalars(select(User))).all()}
    for tid, role in wanted.items():
        user = existing.get(tid)
        if user is None:
            session.add(User(telegram_id=tid, role=role, is_active=True))
        else:
            user.role = role
            user.is_active = True
    for tid, user in existing.items():
        if tid not in wanted:
            user.is_active = False
    await session.commit()

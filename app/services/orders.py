"""Clients, orders and payments received."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import Client, Order, OrderStatus, PaymentStatus, Revenue
from app.services import events

PAYMENT_NAMES = {PaymentStatus.UNPAID: "тўланмаган", PaymentStatus.PARTIAL: "қисман", PaymentStatus.PAID: "тўланган"}
ORDER_NAMES = {OrderStatus.NEW: "янги", OrderStatus.CONFIRMED: "тасдиқланган", OrderStatus.SHIPPED: "жўнатилган", OrderStatus.CANCELLED: "бекор"}


async def create_client(session: AsyncSession, name: str, phone: str | None = None, kind: str | None = None) -> Client:
    client = Client(name=name.strip()[:200], phone=phone, kind=kind)
    session.add(client)
    await session.flush()
    return client


async def list_clients(session: AsyncSession) -> list[Client]:
    return list(await session.scalars(select(Client).order_by(Client.id)))


async def create_order(
    session: AsyncSession,
    client_id: int,
    book_id: int,
    quantity: int,
    unit_price: int,
    idempotency_key: str | None = None,
) -> tuple[Order, bool]:
    """Create an order. The discount is always 0 here: discounts go through approvals."""
    if quantity <= 0 or unit_price < 0:
        raise ValueError("Миқдор мусбат, нарх манфий бўлмаслиги керак.")
    if idempotency_key:
        existing = await session.scalar(select(Order).where(Order.idempotency_key == idempotency_key))
        if existing is not None:
            return existing, False
    order = Order(client_id=client_id, book_id=book_id, quantity=quantity, unit_price=unit_price, idempotency_key=idempotency_key)
    session.add(order)
    await session.flush()
    await events.emit(session, "order_changed", {"order_id": order.id})
    return order, True


async def get_order(session: AsyncSession, order_id: int) -> Order | None:
    return await session.scalar(select(Order).options(selectinload(Order.client), selectinload(Order.book)).where(Order.id == order_id))


async def list_orders(session: AsyncSession, limit: int = 20) -> list[Order]:
    rows = await session.scalars(
        select(Order).options(selectinload(Order.client), selectinload(Order.book)).order_by(Order.id.desc()).limit(limit)
    )
    return list(rows)


def _refresh_payment_status(order: Order) -> None:
    if order.paid_amount <= 0:
        order.payment_status = PaymentStatus.UNPAID
    elif order.paid_amount >= order.total:
        order.payment_status = PaymentStatus.PAID
    else:
        order.payment_status = PaymentStatus.PARTIAL


async def record_payment(session: AsyncSession, order: Order, amount: int, idempotency_key: str | None = None) -> tuple[Revenue, bool]:
    """Record money received from a client (incoming). Outgoing payments are never made by the system."""
    if amount <= 0:
        raise ValueError("Сумма мусбат бўлиши керак.")
    if idempotency_key:
        existing = await session.scalar(select(Revenue).where(Revenue.idempotency_key == idempotency_key))
        if existing is not None:
            return existing, False
    revenue = Revenue(amount=amount, source="order", order_id=order.id, book_id=order.book_id, idempotency_key=idempotency_key)
    session.add(revenue)
    order.paid_amount += amount
    _refresh_payment_status(order)
    await session.flush()
    await events.emit(session, "order_changed", {"order_id": order.id})
    return revenue, True


async def apply_discount(session: AsyncSession, order: Order, percent: float) -> Order:
    """Only called by the approvals service after a director approved the discount."""
    if not 0 <= percent <= 100:
        raise ValueError("Чегирма 0–100% оралиғида бўлиши керак.")
    order.discount_percent = percent
    _refresh_payment_status(order)
    await session.flush()
    await events.emit(session, "order_changed", {"order_id": order.id})
    return order

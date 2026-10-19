"""SQLAlchemy 2 models.

Money is stored as integer so'm (BigInteger): exact arithmetic, no float
rounding, same behaviour on SQLite and PostgreSQL. All timestamps are naive
UTC; conversion to local time happens only when formatting for people.
"""
from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


def _enum(cls: type[enum.Enum], name: str) -> Enum:
    return Enum(cls, name=name, native_enum=False, length=32, values_callable=lambda e: [m.value for m in e])


class Role(str, enum.Enum):
    DIRECTOR = "director"
    STAFF = "staff"
    VIEWER = "viewer"


class BookStage(str, enum.Enum):
    MANUSCRIPT = "manuscript"
    EDITING = "editing"
    PROOFREADING = "proofreading"
    DESIGN = "design"
    LAYOUT = "layout"
    PRINTING = "printing"
    READY = "ready"


class TaskStatus(str, enum.Enum):
    NEW = "new"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    WAITING_APPROVAL = "waiting_approval"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunStatus(str, enum.Enum):
    SUCCESS = "success"
    ERROR = "error"


class PaymentStatus(str, enum.Enum):
    UNPAID = "unpaid"
    PARTIAL = "partial"
    PAID = "paid"


class OrderStatus(str, enum.Enum):
    NEW = "new"
    CONFIRMED = "confirmed"
    SHIPPED = "shipped"
    CANCELLED = "cancelled"


class ApprovalKind(str, enum.Enum):
    PAYMENT = "payment"
    CONTRACT = "contract"
    MASS_MESSAGE = "mass_message"
    DISCOUNT = "discount"
    FINAL_PRINT = "final_print"


class ApprovalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class User(TimestampMixin, Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[Role] = mapped_column(_enum(Role, "role"), nullable=False, default=Role.VIEWER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Book(TimestampMixin, Base):
    __tablename__ = "books"

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    author: Mapped[str | None] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(50), default="book", nullable=False)  # book / textbook / manual
    target_audience: Mapped[str | None] = mapped_column(String(200))
    stage: Mapped[BookStage] = mapped_column(_enum(BookStage, "book_stage"), default=BookStage.MANUSCRIPT, nullable=False)
    print_run: Mapped[int | None] = mapped_column(Integer)  # тираж
    price: Mapped[int | None] = mapped_column(BigInteger)  # sotuv narxi, so'm
    printing_house: Mapped[str | None] = mapped_column(String(200))
    deadline: Mapped[datetime | None] = mapped_column(DateTime)
    manuscript_text: Mapped[str | None] = mapped_column(Text)

    tasks: Mapped[list[Task]] = relationship(back_populates="book")


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[TaskStatus] = mapped_column(_enum(TaskStatus, "task_status"), default=TaskStatus.NEW, nullable=False, index=True)
    assigned_agent: Mapped[str | None] = mapped_column(String(50))
    assignee_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"))
    book_id: Mapped[int | None] = mapped_column(ForeignKey("books.id"))
    step_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    result: Mapped[str | None] = mapped_column(Text)
    reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    overdue_notified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)

    book: Mapped[Book | None] = relationship(back_populates="tasks")
    subtasks: Mapped[list[Task]] = relationship(order_by="Task.step_order")


class AgentRun(TimestampMixin, Base):
    __tablename__ = "agent_runs"

    agent: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"))
    status: Mapped[RunStatus] = mapped_column(_enum(RunStatus, "run_status"), nullable=False)
    input_text: Mapped[str | None] = mapped_column(Text)
    output_text: Mapped[str | None] = mapped_column(Text)
    output_data: Mapped[dict | None] = mapped_column(JSON)
    decision: Mapped[str | None] = mapped_column(String(300))
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    llm_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Client(TimestampMixin, Base):
    __tablename__ = "clients"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50))
    kind: Mapped[str | None] = mapped_column(String(50))  # school / shop / person
    notes: Mapped[str | None] = mapped_column(Text)

    orders: Mapped[list[Order]] = relationship(back_populates="client")


class Order(TimestampMixin, Base):
    __tablename__ = "orders"

    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    discount_percent: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    status: Mapped[OrderStatus] = mapped_column(_enum(OrderStatus, "order_status"), default=OrderStatus.NEW, nullable=False)
    payment_status: Mapped[PaymentStatus] = mapped_column(_enum(PaymentStatus, "payment_status"), default=PaymentStatus.UNPAID, nullable=False)
    paid_amount: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)

    client: Mapped[Client] = relationship(back_populates="orders")
    book: Mapped[Book] = relationship()

    @property
    def total(self) -> int:
        gross = self.quantity * self.unit_price
        return round(gross * (100 - self.discount_percent) / 100)

    @property
    def debt(self) -> int:
        return max(self.total - self.paid_amount, 0)


class Expense(TimestampMixin, Base):
    __tablename__ = "expenses"

    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    book_id: Mapped[int | None] = mapped_column(ForeignKey("books.id"))
    description: Mapped[str | None] = mapped_column(Text)
    is_estimate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    spent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)


class Revenue(TimestampMixin, Base):
    __tablename__ = "revenues"

    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False, default="order")
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"))
    book_id: Mapped[int | None] = mapped_column(ForeignKey("books.id"))
    description: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)


class InventoryItem(TimestampMixin, Base):
    __tablename__ = "inventory"
    __table_args__ = (UniqueConstraint("book_id", "location", name="uq_inventory_book_location"),)

    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), nullable=False)
    location: Mapped[str] = mapped_column(String(100), default="asosiy", nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    book: Mapped[Book] = relationship()


class Approval(TimestampMixin, Base):
    __tablename__ = "approvals"

    kind: Mapped[ApprovalKind] = mapped_column(_enum(ApprovalKind, "approval_kind"), nullable=False)
    status: Mapped[ApprovalStatus] = mapped_column(_enum(ApprovalStatus, "approval_status"), default=ApprovalStatus.PENDING, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    requested_by_agent: Mapped[str | None] = mapped_column(String(50))
    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"))
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    decision_note: Mapped[str | None] = mapped_column(Text)
    execution_result: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)


class AuditLog(TimestampMixin, Base):
    __tablename__ = "audit_logs"

    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity: Mapped[str | None] = mapped_column(String(50))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict | None] = mapped_column(JSON)


class Setting(TimestampMixin, Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)


# Agent workflow tables live in their own module but share this metadata.
from app.agentic import models as _agentic_models  # noqa: F401

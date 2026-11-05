"""Leads and their conversation history (shares the main metadata)."""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models import Base, TimestampMixin, _enum, utcnow


class LeadStage(str, enum.Enum):
    NEW = "new"            # first contact, nothing known yet
    NEED = "need"          # asked what the client wants
    QUANTITY = "quantity"  # product chosen, price told, waiting for quantity
    NAME = "name"
    PHONE = "phone"
    ADDRESS = "address"
    CONFIRM = "confirm"    # summary shown, waiting for "yes"
    WON = "won"            # order accepted
    HANDOFF = "handoff"    # a manager takes over; the bot stays silent
    LOST = "lost"


OPEN_STAGES = {LeadStage.NEW, LeadStage.NEED, LeadStage.QUANTITY, LeadStage.NAME, LeadStage.PHONE, LeadStage.ADDRESS, LeadStage.CONFIRM}


class Lead(TimestampMixin, Base):
    __tablename__ = "leads"

    channel: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # instagram / whatsapp / telegram / messenger / phone
    external_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)  # sender id in that channel
    language: Mapped[str] = mapped_column(String(8), default="uz", nullable=False)
    stage: Mapped[LeadStage] = mapped_column(_enum(LeadStage, "lead_stage"), default=LeadStage.NEW, nullable=False)

    display_name: Mapped[str | None] = mapped_column(String(200))  # profile name from the channel
    name: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(50))
    address: Mapped[str | None] = mapped_column(String(500))

    product_id: Mapped[str | None] = mapped_column(String(64))
    product_name: Mapped[str | None] = mapped_column(String(200))
    unit_price: Mapped[int | None] = mapped_column(BigInteger)
    quantity: Mapped[int | None] = mapped_column(Integer)

    discount_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    last_message_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    # full version: payments, reminders, channel data (e.g. Telegram business_connection_id)
    prepayment_due: Mapped[int | None] = mapped_column(BigInteger)  # amount in the product currency
    currency: Mapped[str | None] = mapped_column(String(8))
    payment_link: Mapped[str | None] = mapped_column(String(500))
    prepaid_at: Mapped[datetime | None] = mapped_column(DateTime)
    reminder_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_reminder_at: Mapped[datetime | None] = mapped_column(DateTime)
    extra: Mapped[dict | None] = mapped_column(JSON)

    messages: Mapped[list[LeadMessage]] = relationship(back_populates="lead", order_by="LeadMessage.id")
    files: Mapped[list[LeadFile]] = relationship(back_populates="lead", order_by="LeadFile.id")

    @property
    def total(self) -> int | None:
        if self.unit_price is None or not self.quantity:
            return None
        return self.unit_price * self.quantity


class LeadMessage(TimestampMixin, Base):
    __tablename__ = "lead_messages"
    __table_args__ = (UniqueConstraint("channel", "external_message_id", name="uq_lead_message_external"),)

    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str] = mapped_column(String(3), nullable=False)  # in / out
    text: Mapped[str] = mapped_column(Text, nullable=False)
    external_message_id: Mapped[str | None] = mapped_column(String(200))  # dedup of webhook retries

    lead: Mapped[Lead] = relationship(back_populates="messages")


class LeadFile(TimestampMixin, Base):
    """A document, photo or voice message a client sent. The bytes are kept on disk under SALES_FILES_DIR."""

    __tablename__ = "lead_files"
    __table_args__ = (UniqueConstraint("channel", "external_message_id", name="uq_lead_file_external"),)

    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # document / photo / audio
    file_name: Mapped[str] = mapped_column(String(200), nullable=False)
    mime: Mapped[str | None] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    path: Mapped[str | None] = mapped_column(String(500))  # None -> download failed, only the reference is kept
    caption: Mapped[str | None] = mapped_column(Text)
    transcript: Mapped[str | None] = mapped_column(Text)  # voice messages
    external_message_id: Mapped[str | None] = mapped_column(String(200))

    lead: Mapped[Lead] = relationship(back_populates="files")

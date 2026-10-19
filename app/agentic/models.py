"""Durable workflow state for the tool-calling agent engine.

A Workflow is one goal given by a person ("open a textbook project, review
chapter one, ..."). Its conversation with the model, the tool calls still
waiting for an approval, and every step are stored, so a workflow survives a
restart and continues after a director's decision.
"""
from __future__ import annotations

import enum

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models import Base, TimestampMixin, _enum


class WorkflowStatus(str, enum.Enum):
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    DONE = "done"
    FAILED = "failed"


class Workflow(TimestampMixin, Base):
    __tablename__ = "workflows"

    goal: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[WorkflowStatus] = mapped_column(_enum(WorkflowStatus, "workflow_status"), default=WorkflowStatus.RUNNING, nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    messages: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    pending_calls: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # tool calls not yet executed
    waiting_call_id: Mapped[str | None] = mapped_column(String(100))
    waiting_approval_id: Mapped[int | None] = mapped_column(ForeignKey("approvals.id"))
    result: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    model_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)


class WorkflowStep(TimestampMixin, Base):
    __tablename__ = "workflow_steps"

    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # model | tool | approval
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # ok | error | rejected | waiting | resumed
    args: Mapped[dict | None] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON)
    duration_ms: Mapped[float] = mapped_column(Float, default=0, nullable=False)

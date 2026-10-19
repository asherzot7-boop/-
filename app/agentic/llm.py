"""Chat models that can call tools.

- OpenAIToolModel: real model through the OpenAI tools API.
- RuleToolModel: no API key needed. Turns the goal into the same tool calls
  with the keyword rules of the classic coordinator, one call per turn, so
  the whole engine (tools, permissions, approvals, persistence) runs in demo
  mode and in tests.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Protocol

from app.agents.coordinator import classify_rules
from app.agents.textutil import extract_body_text, extract_id, extract_percent, extract_title


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text from the model; validated before use


@dataclass
class ModelTurn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0


class ToolModel(Protocol):
    name: str

    async def respond(self, messages: list[dict], tools: list[dict]) -> ModelTurn: ...


class OpenAIToolModel:
    def __init__(self, api_key: str, model: str, timeout: float = 60.0) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout, max_retries=2)
        self.name = model

    async def respond(self, messages: list[dict], tools: list[dict]) -> ModelTurn:
        resp = await self._client.chat.completions.create(model=self.name, messages=messages, tools=tools or None, temperature=0.1)
        msg = resp.choices[0].message
        calls = [ToolCall(c.id, c.function.name, c.function.arguments or "{}") for c in (msg.tool_calls or [])]
        usage = getattr(resp, "usage", None)
        return ModelTurn(msg.content or "", calls, getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0)


_PRINT = re.compile(r"босмахона|босмага|чоп\s+эт", re.IGNORECASE)
_CREATE = re.compile(r"(оч|яра|янги)", re.IGNORECASE)
_GRADE = re.compile(r"(\d{1,2})\s*-?\s*синф", re.IGNORECASE)


def _plan(goal: str) -> list[tuple[str, dict]]:
    """Goal -> ordered tool calls. '$book' is filled with the id created earlier."""
    body = extract_body_text(goal)
    grade = _GRADE.search(goal)
    out: list[tuple[str, dict]] = []
    for step in classify_rules(goal):
        low = step.instruction.lower()
        ref = extract_id(step.instruction)
        if step.agent == "production":
            if _PRINT.search(low):
                out.append(("send_to_print", {"book_id": ref or "$book"}))
            elif _CREATE.search(low) and any(w in low for w in ("лойиҳа", "китоб", "дарслик")):
                out.append(("create_book", {"title": extract_title(goal) or "Янги дарслик лойиҳаси", "kind": "textbook" if "дарслик" in low else "book"}))
            else:
                out.append(("list_books", {}))
        elif step.agent == "editor":
            out.append(("review_text", {"text": body or ""}))
        elif step.agent == "methodist":
            out.append(("plan_textbook", {"text": body or "", "grade": int(grade.group(1)) if grade else None}))
        elif step.agent == "sales":
            pct = extract_percent(step.instruction)
            if "чегирма" in low and ref and pct is not None:
                out.append(("request_discount", {"order_id": ref, "percent": pct}))
            else:
                out.append(("sales_report", {}))
        elif step.agent == "finance":
            if "таннарх" in low:
                out.append(("book_cost", {"book_id": ref or "$book"}))
            else:
                out.append(("finance_report", {}))
    return out


class RuleToolModel:
    name = "rules"

    async def respond(self, messages: list[dict], tools: list[dict]) -> ModelTurn:
        goal = next(m["content"] for m in messages if m["role"] == "user")
        plan = _plan(goal)
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        done = len(results)
        if done < len(plan):
            name, args = plan[done]
            book = next((r.get("book_id") for r in reversed(results) if isinstance(r, dict) and r.get("book_id")), None)
            args = {k: (book if v == "$book" else v) for k, v in args.items()}
            args = {k: v for k, v in args.items() if not (k == "book_id" and v in (None, "$book"))}
            return ModelTurn(tool_calls=[ToolCall(f"call_{done + 1}", name, json.dumps(args, ensure_ascii=False))])
        if not plan:
            return ModelTurn(text="Бу вазифа учун мос асбоб топилмади. Аниқроқ ёзинг ёки ходимга топширинг.")
        lines = []
        for (name, _), r in zip(plan, results):
            lines.append(f"• {name}: {r.get('summary') or r.get('error') or 'бажарилди'}")
        return ModelTurn(text="Натижа:\n" + "\n".join(lines))

"""Langfuse 对话级 Trace 与业务事件观测。"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.infrastructure.eventbus import TradeEvent
from app.infrastructure.settings import Settings

logger = logging.getLogger(__name__)

_SECRET_PATTERN = re.compile(r"(?i)(sk-[\w-]+|pk-[\w-]+|bearer\s+[\w.-]+)")
_EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE_PATTERN = re.compile(r"(?<!\w)(?:\+?\d(?:[\s().-]*\d){9,14})(?!\w)")
_CARD_PATTERN = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_MAX_TRACE_TEXT = 4_000


@dataclass
class _Turn:
    root: Any
    scope: Any
    attributes_scope: Any


class LangfuseObserver:
    """可选依赖；观测上报失败永远不阻断购物 Agent。"""

    def __init__(self, settings: Settings) -> None:
        self._client: Any = None
        self._turns: dict[str, _Turn] = {}
        if not (settings.langfuse_base_url and settings.langfuse_public_key and settings.langfuse_secret_key):
            return
        from langfuse import Langfuse

        self._client = Langfuse(
            base_url=settings.langfuse_base_url,
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            environment=settings.langfuse_environment,
            mask=_mask_data,
            mask_otel_spans=_mask_otel_spans,
        )

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def start_turn(self, session_id: str, buyer_id: str, query: str) -> None:
        if not self._client:
            return
        attributes_scope = None
        try:
            from langfuse import propagate_attributes

            prompt_path = Path(__file__).resolve().parent.parent / "application" / "prompts" / "shopcross.yml"
            prompt_version = hashlib.sha256(prompt_path.read_bytes()).hexdigest()[:12]
            attributes_scope = propagate_attributes(
                session_id=session_id,
                user_id=buyer_id,
                tags=["shopping", "agent", "api"],
                metadata={"feature": "cross_border_shopping", "route": "POST /commerce/intents"},
            )
            attributes_scope.__enter__()
            scope = self._client.start_as_current_observation(
                name="shopping-agent-turn",
                as_type="agent",
                input={"query": _redact(query)},
                metadata={
                    "feature": "cross_border_shopping",
                    "route": "POST /commerce/intents",
                    "prompt_fingerprint": prompt_version,
                },
                version=prompt_version,
            )
            root = scope.__enter__()
            self._turns[session_id] = _Turn(
                root=root, scope=scope, attributes_scope=attributes_scope,
            )
        except Exception as err:  # noqa: BLE001
            if attributes_scope is not None:
                attributes_scope.__exit__(None, None, None)
            logger.warning("Langfuse Trace 创建失败，已跳过：%s", err)

    def record_event(self, event: TradeEvent) -> None:
        turn = self._turns.get(event.shopping_session_id)
        if turn is None or event.type in {
            "token.delta", "final.result", "tool.invoke", "agent.dispatch",
        }:
            return
        try:
            if event.type == "tool.result" and not _is_retrieval(event.payload):
                # AgentScope 已追踪完整工具生命周期，避免再生成平行的 tool.result 节点。
                return
            event_type = "retriever" if event.type == "tool.result" else "event"
            level = "ERROR" if event.type == "error" else "DEFAULT"
            child = self._client.start_observation(
                name=_event_name(event),
                as_type=event_type,
                input=_redact(event.payload) if event.type != "tool.result" else None,
                output=_redact(event.payload) if event.type == "tool.result" else None,
                metadata={"occurred_at": event.occurred_at},
                level=level,
            )
            child.end()
        except Exception as err:  # noqa: BLE001
            logger.debug("Langfuse 事件上报跳过：%s", err)

    def finish_turn(self, session_id: str, output: str, duration_ms: int) -> None:
        turn = self._turns.pop(session_id, None)
        if turn is None:
            return
        try:
            turn.root.update(output={"answer": _redact(output)}, metadata={"duration_ms": duration_ms})
            turn.scope.__exit__(None, None, None)
            turn.attributes_scope.__exit__(None, None, None)
            self._client.flush()
        except Exception as err:  # noqa: BLE001
            logger.warning("Langfuse Trace 收尾失败：%s", err)

    def shutdown(self) -> None:
        if self._client:
            self._client.flush()

def _is_retrieval(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    return (
        "hits" in payload
        or "recall_strategy" in payload
        or payload.get("tool") in {"product_search_tool", "category_insight_tool", "web_search_tool"}
    )


def _event_name(event: TradeEvent) -> str:
    if event.type != "tool.result" or not isinstance(event.payload, dict):
        return event.type
    tool = event.payload.get("tool", "retrieval")
    return f"retrieval.{tool}"


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    text = str(value)
    for pattern in (_SECRET_PATTERN, _EMAIL_PATTERN, _PHONE_PATTERN, _CARD_PATTERN):
        text = pattern.sub("[REDACTED]", text)
    return text[:_MAX_TRACE_TEXT]


def _mask_data(*, data: Any, **_: Any) -> Any:
    """覆盖 SDK 手工 observation 的输入、输出与 metadata。"""
    return _redact(data)


def _mask_otel_spans(*, params: Any) -> Any:
    """覆盖 AgentScope 等第三方 OTel instrumentation，数据离开进程前脱敏。"""
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

    patches: dict[Any, Any] = {}
    for identifier, span in params.spans.items():
        replacements: dict[str, Any] = {}
        for key, value in span.attributes.items():
            if isinstance(value, str):
                masked = _redact(value)
                if masked != value:
                    replacements[key] = masked
        if replacements:
            patches[identifier] = OtelSpanPatch(set_attributes=replacements)
    return MaskOtelSpansResult(span_patches=patches)

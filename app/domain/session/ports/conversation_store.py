# -*- coding: utf-8 -*-
"""
ConversationStore 对话存储端口（Port）

===========================================
为什么需要 ConversationStore？
===========================================
SessionStore 存的是 AgentState 快照（框架内部结构），不可读。
ConversationStore 存的是业务可读的对话流水：
- 谁在什么时候说了什么（turn）
- 调了哪些工具、工具返回什么（event）

用途：
1. 事后追溯：客服可以查看完整对话过程
2. 前端恢复：刷新页面后可以恢复对话历史
3. 自进化飞轮：bad case 采集与回放

===========================================
两套实现
===========================================
- JsonFileConversationStore: JSONL 文件存储（本地开发）
- SqlConversationStore: SQLite 存储（生产）
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

# 有效的角色列表
VALID_ROLES = ("buyer", "agent")


def _now_iso() -> str:
    """
    获取当前 UTC 时间的 ISO 格式字符串

    返回:
        ISO 格式时间字符串（如 "2026-09-06T10:30:00+00:00"）
    """
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ConversationTurn:
    """
    一轮完整问答（值对象）

    代表一次"买家提问 + Agent 回答"的完整交互。

    属性:
        session_id: 会话 ID
        buyer_id: 买家 ID
        role: 角色（"buyer" 或 "agent"）
        content: 消息内容
        model: 使用的模型（agent 回复时记录）
        latency_ms: 响应耗时（毫秒）
        created_at: 创建时间

    不变量:
        - role 必须是 "buyer" 或 "agent"
        - session_id 不能为空
    """

    session_id: str                    # 会话 ID
    buyer_id: str                      # 买家 ID
    role: str                          # buyer / agent
    content: str                       # 消息内容
    model: str = ""                    # 使用的模型
    latency_ms: int = 0                # 响应耗时
    created_at: str = field(default_factory=_now_iso)  # 创建时间

    def __post_init__(self) -> None:
        """创建后的验证（不变量检查）"""
        if self.role not in VALID_ROLES:
            raise ValueError(f"ConversationTurn.role 必须是 {VALID_ROLES}：{self.role}")
        if not self.session_id:
            raise ValueError("ConversationTurn.session_id required")


@dataclass(frozen=True)
class ConversationEventRecord:
    """
    过程事件记录（值对象）

    对应 TradeEventBus 的一条事件，持久化后供回放。

    属性:
        session_id: 会话 ID
        type: 事件类型（如 "tool.invoke"、"tool.result"）
        payload: 事件数据
        occurred_at: 发生时间
    """

    session_id: str                # 会话 ID
    type: str                      # 事件类型
    payload: dict[str, Any]        # 事件数据
    occurred_at: str = field(default_factory=_now_iso)  # 发生时间


class ConversationStore(ABC):
    """
    对话存储接口（抽象基类）

    负责对话流水和过程事件的存取。
    """

    @abstractmethod
    async def append_turn(self, turn: ConversationTurn) -> None:
        """
        追加一轮问答

        参数:
            turn: 问答记录
        """
        ...

    @abstractmethod
    async def append_events(self, events: list[ConversationEventRecord]) -> None:
        """
        批量追加过程事件

        参数:
            events: 事件列表

        注意:
            一轮结束后一次性写，减少数据库往返
        """
        ...

    @abstractmethod
    async def list_turns(self, session_id: str, limit: int = 50) -> list[ConversationTurn]:
        """
        获取会话的对话流水

        参数:
            session_id: 会话 ID
            limit: 返回数量上限

        返回:
            问答列表（按 turn_index 升序）
        """
        ...

    @abstractmethod
    async def list_events(self, session_id: str, limit: int = 200) -> list[ConversationEventRecord]:
        """
        获取会话的过程事件

        参数:
            session_id: 会话 ID
            limit: 返回数量上限

        返回:
            事件列表（按发生顺序）
        """
        ...

    @abstractmethod
    async def touch_session(
        self,
        session_id: str,
        buyer_id: str,
        locale: str,
        currency: str,
    ) -> None:
        """
        确保会话主记录存在（upsert）

        参数:
            session_id: 会话 ID
            buyer_id: 买家 ID
            locale: 语言区域
            currency: 币种

        注意:
            如果会话不存在则创建，存在则刷新活跃时间
        """
        ...

    @abstractmethod
    async def find_session(self, session_id: str) -> Optional[dict]:
        """
        查找会话主记录

        参数:
            session_id: 会话 ID

        返回:
            会话主记录，不存在返回 None
        """
        ...

    @abstractmethod
    async def list_sessions(self, buyer_id: str, limit: int = 30) -> list[dict]:
        """
        获取买家的会话摘要列表

        参数:
            buyer_id: 买家 ID
            limit: 返回数量上限

        返回:
            会话摘要列表（按最近活跃时间降序）
        """
        ...

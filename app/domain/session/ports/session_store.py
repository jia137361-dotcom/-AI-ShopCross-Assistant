# -*- coding: utf-8 -*-
"""
SessionStore 会话存储端口（Port）

===========================================
什么是 AgentState 快照？
===========================================
AgentState 是 Agent 的"记忆"，包含：
- 对话历史（context）
- 当前任务列表（tasks_context）
- 摘要（summary）

每轮对话结束后，把 AgentState 序列化为 JSON 字符串保存。
服务重启后，可以恢复 AgentState，继续多轮对话。

===========================================
为什么接口是 async 的？
===========================================
- 文件实现：同步即可完成（`open()` + `write()`）
- 数据库实现：必须异步（`await db.execute()`）

端口按更严格的一方（async）定义，避免换实现时改调用方。

===========================================
两套实现
===========================================
- JsonFileSessionStore: JSON 文件存储（本地开发）
- SqlSessionStore: SQLite 存储（生产）

区别：
- SessionStore: 存 AgentState 快照（框架内部结构，只为恢复上下文）
- ConversationStore: 存业务可读的对话流水（谁在什么时候说了什么）
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class SessionStore(ABC):
    """
    会话存储接口（抽象基类）

    负责 AgentState 快照的存取。
    """

    @abstractmethod
    async def save(self, session_id: str, state_json: str) -> None:
        """
        保存 AgentState 快照

        参数:
            session_id: 会话 ID（如 "session-abc123"）
            state_json: AgentState 的 JSON 字符串

        注意:
            如果快照已存在，会覆盖（每轮更新）
        """
        ...

    @abstractmethod
    async def load(self, session_id: str) -> Optional[str]:
        """
        读取 AgentState 快照

        参数:
            session_id: 会话 ID

        返回:
            AgentState 的 JSON 字符串，不存在返回 None

        示例:
            >>> state_json = await store.load("session-abc123")
            >>> if state_json:
            ...     state = AgentState.model_validate_json(state_json)
        """
        ...

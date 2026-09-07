# -*- coding: utf-8 -*-
"""
ShoppingContext 购物上下文（Shopping Context）

===========================================
什么是 ContextVar？
===========================================
ContextVar 是 Python 的"协程局部变量"：
- 每个协程有独立的值
- 子协程继承父协程的值
- 不同协程互不干扰

===========================================
为什么需要 ShoppingContext？
===========================================
在 Agent 执行过程中，很多组件需要知道"当前是哪个买家在请求"：
- 工具需要 buyer_id 来创建订单
- 缓存需要 session_id 来分桶
- 事件总线需要 session_id 来路由

如果层层透传参数，代码会很乱：
    async def tool(buyer_id, session_id, ...):
        await sub_tool(buyer_id, session_id, ...)

解决方案：用 ContextVar 保存当前任务的会话快照，任何组件随时读取。

===========================================
多用户并发不会串台
===========================================
asyncio 的每个 Task 有独立的 ContextVar 值。
买家 A 和买家 B 的请求在不同 Task 中执行，互不干扰。
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ShoppingContextSnapshot:
    """
    购物上下文快照（值对象）

    保存当前请求的关键信息。

    属性:
        shopping_session_id: 会话 ID
        buyer_id: 买家 ID
        locale: 语言区域（如 "zh-CN"）
        currency: 币种（如 "CNY"）
    """

    shopping_session_id: str  # 会话 ID
    buyer_id: str             # 买家 ID
    locale: str               # 语言区域
    currency: str             # 币种


# ContextVar：全局的上下文变量（每个协程独立）
_current_snapshot: ContextVar[Optional[ShoppingContextSnapshot]] = ContextVar(
    "shopcross_shopping_context",
    default=None,
)


class ShoppingContext:
    """
    购物上下文（静态工具类）

    提供设置、获取、重置上下文的方法。
    """

    @staticmethod
    def set(snapshot: ShoppingContextSnapshot):
        """
        设置当前上下文

        参数:
            snapshot: 上下文快照

        返回:
            token（用于后续 reset）

        示例:
            >>> token = ShoppingContext.set(ShoppingContextSnapshot(...))
            >>> # ... 执行请求 ...
            >>> ShoppingContext.reset(token)
        """
        return _current_snapshot.set(snapshot)

    @staticmethod
    def reset(token) -> None:
        """
        重置上下文（恢复到之前的值）

        参数:
            token: set() 返回的 token

        注意:
            请求结束后必须调用，避免上下文泄漏
        """
        _current_snapshot.reset(token)

    @staticmethod
    def current() -> Optional[ShoppingContextSnapshot]:
        """
        获取当前上下文

        返回:
            当前上下文快照，未设置时返回 None

        示例:
            >>> ctx = ShoppingContext.current()
            >>> if ctx:
            ...     print(ctx.buyer_id)
        """
        return _current_snapshot.get()

    @staticmethod
    def current_session_id() -> str:
        """
        获取当前会话 ID

        返回:
            当前会话 ID，未设置时返回 "anonymous"

        示例:
            >>> session_id = ShoppingContext.current_session_id()
        """
        snapshot = _current_snapshot.get()
        return snapshot.shopping_session_id if snapshot else "anonymous"

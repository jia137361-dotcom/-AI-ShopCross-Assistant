# -*- coding: utf-8 -*-
"""
TradeEventBus 事件总线（Event Bus）

===========================================
什么是事件总线？
===========================================
事件总线是一个"消息中心"：
- 发布者（Publisher）发布事件
- 订阅者（Subscriber）订阅事件
- 事件总线负责把事件路由给所有订阅者

===========================================
两种订阅方式
===========================================
1. **本地订阅**（subscribe）：
   - 本进程内的订阅者（如 WebSocket 连接）
   - 通过 asyncio.Queue 实现

2. **跨进程订阅**（backplane）：
   - 通过 Redis Pub/Sub 实现
   - worker 进程发布的事件，API 进程可以收到

===========================================
事件类型
===========================================
    agent.dispatch      子 Agent 被调度
    tool.invoke         工具开始执行
    tool.result         工具执行完成
    token.delta         流式 token 增量
    plan.update         Task 计划变更
    context.compressed  上下文压缩发生
    model.fallback      主模型回退到备用模型
    cache.hit           语义缓存命中
    task.queued         意图已入队
    task.started        worker 已开始处理
    final.result        最终回复
    error               异常

===========================================
为什么 publish 保持同步签名？
===========================================
十几处调用方不必改。
广播用 fire-and-forget 任务发出（不等待完成）。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# 事件类型（字符串）
TradeEventType = str

# 所有合法的事件类型
EVENT_TYPES = (
    "agent.dispatch",
    "tool.invoke",
    "tool.result",
    "token.delta",
    "plan.update",
    "context.compressed",
    "model.fallback",
    "cache.hit",
    "task.queued",
    "task.started",
    "final.result",
    "error",
)


@dataclass(frozen=True)
class TradeEvent:
    """
    交易事件（值对象）

    属性:
        shopping_session_id: 会话 ID
        type: 事件类型
        payload: 事件数据
        occurred_at: 发生时间
    """

    shopping_session_id: str  # 会话 ID
    type: TradeEventType       # 事件类型
    payload: Any               # 事件数据
    occurred_at: str           # 发生时间

    def to_dict(self) -> dict:
        """
        序列化为字典（用于 Redis 传输）

        返回:
            字典表示
        """
        return {
            "shopping_session_id": self.shopping_session_id,
            "type": self.type,
            "payload": self.payload,
            "occurred_at": self.occurred_at,
        }

    @staticmethod
    def from_dict(raw: dict) -> "TradeEvent":
        """
        从字典反序列化（从 Redis 读取）

        参数:
            raw: 字典数据

        返回:
            TradeEvent 实例
        """
        return TradeEvent(
            shopping_session_id=raw["shopping_session_id"],
            type=raw["type"],
            payload=raw.get("payload"),
            occurred_at=raw.get("occurred_at", ""),
        )


@dataclass
class TradeEventBus:
    """
    事件总线（asyncio 版发布订阅）

    每个订阅者一个独立 Queue，互不阻塞。

    属性:
        _subscribers: 会话 ID → 订阅者队列列表
        _backplane: 跨进程背板（Redis Pub/Sub）
        _observers: 观察者列表（如 Langfuse）
        _pending: 待完成的广播任务（强引用防 GC）
    """

    _subscribers: dict[str, list[asyncio.Queue]] = field(default_factory=dict)
    _backplane: Any = None  # EventBackplane | None
    _observers: list[Any] = field(default_factory=list)
    _pending: set = field(default_factory=set)

    def attach_backplane(self, backplane: Any) -> None:
        """
        附加跨进程背板

        参数:
            backplane: 背板实例（如 RedisEventBackplane）
        """
        self._backplane = backplane

    def attach_observer(self, observer: Any) -> None:
        """
        附加观察者

        参数:
            observer: 观察者实例（如 LangfuseObserver）
        """
        self._observers.append(observer)

    def subscribe(self, shopping_session_id: str) -> asyncio.Queue:
        """
        订阅会话事件

        参数:
            shopping_session_id: 会话 ID

        返回:
            事件队列（订阅者从队列中读取事件）

        示例:
            >>> queue = bus.subscribe("session-abc")
            >>> event = await queue.get()
        """
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(shopping_session_id, []).append(queue)
        return queue

    def unsubscribe(self, shopping_session_id: str, queue: asyncio.Queue) -> None:
        """
        取消订阅

        参数:
            shopping_session_id: 会话 ID
            queue: 之前 subscribe 返回的队列

        注意:
            如果该会话没有其他订阅者，会清理订阅列表
        """
        queues = self._subscribers.get(shopping_session_id, [])
        if queue in queues:
            queues.remove(queue)
        if not queues:
            self._subscribers.pop(shopping_session_id, None)

    def deliver_local(self, event: TradeEvent) -> None:
        """
        只投递给本进程订阅者

        参数:
            event: 事件

        注意:
            背板收到远端事件后走这里，避免回环广播
            （否则事件会投递两次）
        """
        for queue in self._subscribers.get(event.shopping_session_id, []):
            queue.put_nowait(event)

    def publish(self, shopping_session_id: str, event_type: TradeEventType, payload: Any) -> None:
        """
        发布事件

        参数:
            shopping_session_id: 会话 ID
            event_type: 事件类型
            payload: 事件数据

        流程:
            1. 创建事件对象
            2. 投递给本进程订阅者
            3. 通知观察者（如 Langfuse）
            4. 广播到 Redis（跨进程）

        注意:
            - 广播是 fire-and-forget（不等待完成）
            - 观测故障不影响业务链路
        """
        if event_type not in EVENT_TYPES:
            raise ValueError(f"未知事件类型：{event_type}")
        event = TradeEvent(
            shopping_session_id=shopping_session_id,
            type=event_type,
            payload=payload,
            occurred_at=datetime.now(timezone.utc).isoformat(),
        )
        self.deliver_local(event)
        for observer in self._observers:
            try:
                observer.record_event(event)
            except Exception:
                pass  # 观测故障不可影响业务链路
        self._broadcast(event)

    def _broadcast(self, event: TradeEvent) -> None:
        """
        广播到 Redis（跨进程）

        参数:
            event: 事件

        注意:
            - 没有背板时不广播
            - 强引用挂在集合里，否则任务可能在完成前被 GC 回收
            - 没有运行中的事件循环时跳过（如同步测试）
        """
        if self._backplane is None:
            return
        try:
            task = asyncio.create_task(self._backplane.publish(event))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)
        except RuntimeError:
            # 没有运行中的事件循环（例如同步测试里直接调 publish）
            pass

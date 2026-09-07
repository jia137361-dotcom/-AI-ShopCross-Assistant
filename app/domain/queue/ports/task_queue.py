# -*-future__ annotations -*-
"""
TaskQueue 任务队列端口（Port）+ 任务值对象

===========================================
什么是任务队列？
===========================================
任务队列是一个"缓冲区"：
- 生产者（API 进程）把任务放入队列
- 消费者（worker 进程）从队列取出任务处理

好处：
1. 削峰：大量请求同时涌入时，不会打垮服务
2. 解耦：API 和 worker 独立部署、独立扩展
3. 可靠：任务不会丢失（持久化到 Redis）

===========================================
什么是 at-least-once？
===========================================
投递语义：任务**至少会被处理一次**，可能被处理多次。

原因：
- Redis Stream 重投：worker 崩溃后未 ack 的消息会被重新领取
- worker 崩溃重启：正在处理的任务可能被重复处理

所以**调用方必须自己保证幂等**：
- create_order 是写操作，重复消费 = 重复下单
- 解决方案：幂等键（同一会话同一问句只入队一次）

===========================================
什么是优先级队列？
===========================================
长会话（轮数多、上下文大）单次耗时明显更长。
如果把长会话和短会话混在同一个队列，短会话会被堵在后面。

解决方案：双队列优先级
- 正常队列（priority=0）：短平快的新会话
- 大请求队列（priority=1）：长会话

Redis 按传入顺序返回，正常流排前面，所以短会话优先被消费。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def _now_iso() -> str:
    """
    获取当前 UTC 时间的 ISO 格式字符串

    返回:
        ISO 格式时间字符串
    """
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class IntentTask:
    """
    意图任务（值对象）

    代表一个买家提交的购物意图。

    属性:
        task_id: 任务 ID（如 "task-abc123"）
        shopping_session_id: 会话 ID
        buyer_id: 买家 ID
        locale: 语言区域
        currency: 币种
        raw_query: 原始查询文本
        enqueued_at: 入队时间
        priority: 优先级（0=正常，1=大请求）
    """

    task_id: str                        # 任务 ID
    shopping_session_id: str            # 会话 ID
    buyer_id: str                       # 买家 ID
    locale: str                         # 语言区域
    currency: str                       # 币种
    raw_query: str                      # 原始查询
    enqueued_at: str = field(default_factory=_now_iso)  # 入队时间
    priority: int = 0                   # 优先级（0=正常，1=大请求）

    def to_dict(self) -> dict:
        """
        序列化为字典（用于存入 Redis）

        返回:
            字典表示
        """
        return {
            "task_id": self.task_id,
            "shopping_session_id": self.shopping_session_id,
            "buyer_id": self.buyer_id,
            "locale": self.locale,
            "currency": self.currency,
            "raw_query": self.raw_query,
            "enqueued_at": self.enqueued_at,
            "priority": self.priority,
        }

    @staticmethod
    def from_dict(raw: dict) -> "IntentTask":
        """
        从字典反序列化（从 Redis 读取）

        参数:
            raw: 字典数据

        返回:
            IntentTask 实例
        """
        return IntentTask(
            task_id=raw["task_id"],
            shopping_session_id=raw["shopping_session_id"],
            buyer_id=raw.get("buyer_id", ""),
            locale=raw.get("locale", "zh-CN"),
            currency=raw.get("currency", "CNY"),
            raw_query=raw.get("raw_query", ""),
            enqueued_at=raw.get("enqueued_at", ""),
            priority=int(raw.get("priority", 0)),
        )


@dataclass(frozen=True)
class TaskStatus:
    """
    任务状态（值对象）

    属性:
        task_id: 任务 ID
        state: 状态（queued / running / done / failed）
        final_text: 最终回复（done 时）
        error: 错误信息（failed 时）
        queue_position: 队列位置（queued 时）
    """

    task_id: str              # 任务 ID
    state: str                # queued / running / done / failed
    final_text: str = ""      # 最终回复
    error: str = ""           # 错误信息
    queue_position: int = 0   # 队列位置


class TaskQueue(ABC):
    """
    任务队列接口（抽象基类）

    定义了任务队列的标准操作。

    实现:
        - RedisStreamTaskQueue: Redis Stream 实现
        - 可替换为 RabbitMQ / Kafka 实现
    """

    @abstractmethod
    async def enqueue(self, task: IntentTask) -> None:
        """
        入队

        参数:
            task: 要入队的任务
        """
        ...

    @abstractmethod
    async def set_status(self, status: TaskStatus) -> None:
        """
        设置任务状态

        参数:
            status: 任务状态

        状态流转:
            queued → running → done
                      ↘ failed
        """
        ...

    @abstractmethod
    async def get_status(self, task_id: str) -> Optional[TaskStatus]:
        """
        获取任务状态

        参数:
            task_id: 任务 ID

        返回:
            任务状态，不存在返回 None
        """
        ...

    @abstractmethod
    async def depth(self) -> int:
        """
        获取队列深度（待消费任务数）

        返回:
            待消费任务数

        用途:
            /health 接口和容量观察
        """
        ...

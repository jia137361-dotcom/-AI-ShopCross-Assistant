# -*- coding: utf-8 -*-
"""
OrderRepository 订单仓储端口（Port）

===========================================
两套实现
===========================================
- InMemoryOrderRepository: 基于内存字典（本地开发）
- SqlOrderRepository: SQLite 存储（生产）

===========================================
为什么需要 next_order_id()？
===========================================
订单号需要自增（GBX-000001, GBX-000002, ...）。
这个逻辑放在仓储层，因为不同的存储方式生成 ID 的方式不同：
- 内存：用 itertools.count()
- 数据库：用自增主键或序列
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from app.domain.order.order import Order


class OrderRepository(ABC):
    """
    订单仓储接口（抽象基类）

    定义了订单数据访问的标准操作。
    """

    @abstractmethod
    async def save(self, order: Order) -> None:
        """
        保存订单

        参数:
            order: 要保存的订单

        注意:
            如果订单已存在，会覆盖（ upsert 语义）
        """
        ...

    @abstractmethod
    async def find_by_id(self, order_id: str) -> Optional[Order]:
        """
        根据订单号查找订单

        参数:
            order_id: 订单号（如 "GBX-000001"）

        返回:
            找到的订单，未找到返回 None

        示例:
            >>> order = await repo.find_by_id("GBX-000001")
        """
        ...

    @abstractmethod
    async def next_order_id(self) -> str:
        """
        生成下一个订单号

        返回:
            新的订单号（如 "GBX-000002"）

        示例:
            >>> order_id = await repo.next_order_id()  # "GBX-000002"
        """
        ...

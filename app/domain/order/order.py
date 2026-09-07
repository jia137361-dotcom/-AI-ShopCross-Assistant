# -*- coding: utf-8 -*-
"""
Order 聚合根（Aggregate Root）

===========================================
什么是状态机？（State Machine）
===========================================
状态机是描述一个对象在不同状态之间转换的规则。

订单的状态流转：
                    ┌──────────────┐
                    │    DRAFT     │  （草稿，刚创建）
                    └──────┬───────┘
                           │ confirm()
                           ▼
                    ┌──────────────┐
                    │  CONFIRMED   │  （已确认，等待发货）
                    └──────┬───────┘
                           │ cancel(reason)
                           ▼
                    ┌──────────────┐
                    │  CANCELLED   │  （已取消）
                    └──────────────┘

规则：
- DRAFT 只能转为 CONFIRMED（不能跳过）
- CONFIRMED 只能转为 CANCELLETED（不能回退）
- 取消必须提供原因（reason）

===========================================
为什么金额只在 snapshot() 中出现？
===========================================
这是一个重要的安全设计：
- Agent 不允许自行计算金额（防止幻觉/篡改）
- 所有金额数字必须来自工具返回的 snapshot
- 这样可以防止模型"编造"价格
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from app.domain.catalog.money import Money
from app.domain.order.address import Address
from app.domain.order.order_line import OrderLine


class OrderStatus(str, Enum):
    """
    订单状态枚举

    使用 str 枚举，方便序列化和数据库存储
    """
    DRAFT = "DRAFT"           # 草稿
    CONFIRMED = "CONFIRMED"   # 已确认
    CANCELLED = "CANCELLED"   # 已取消


def _now() -> datetime:
    """
    获取当前 UTC 时间（辅助函数）

    返回:
        当前时间（UTC 时区）
    """
    return datetime.now(timezone.utc)


@dataclass
class Order:
    """
    订单聚合根

    代表一个完整的订单，包含订单行、收货地址等信息。

    属性:
        order_id: 订单号（如 "GBX-000001"）
        buyer_id: 买家 ID
        shipping_address: 收货地址
        lines: 订单行列表（至少一条）
        status: 订单状态（默认 DRAFT）
        created_at: 创建时间
        confirmed_at: 确认时间（可为空）
        cancelled_at: 取消时间（可为空）
        cancel_reason: 取消原因（可为空）

    不变量:
        - order_id 不能为空
        - buyer_id 不能为空
        - 至少要有一条订单行
        - 所有订单行的币种必须一致
    """

    order_id: str                                  # 订单号
    buyer_id: str                                  # 买家 ID
    shipping_address: Address                      # 收货地址
    lines: list[OrderLine]                         # 订单行列表
    status: OrderStatus = OrderStatus.DRAFT        # 订单状态
    created_at: datetime = field(default_factory=_now)  # 创建时间
    confirmed_at: Optional[datetime] = None        # 确认时间
    cancelled_at: Optional[datetime] = None        # 取消时间
    cancel_reason: Optional[str] = None            # 取消原因

    def __post_init__(self) -> None:
        """
        创建后的验证（不变量检查）

        确保:
        1. order_id 不能为空
        2. buyer_id 不能为空
        3. 至少要有一条订单行
        4. 所有订单行的币种必须一致

        异常:
            ValueError: 验证失败
        """
        if not self.order_id:
            raise ValueError("Order.order_id required")
        if not self.buyer_id:
            raise ValueError("Order.buyer_id required")
        if not self.lines:
            raise ValueError(f"Order 至少要有一条订单行：{self.order_id}")
        # 检查所有订单行的币种是否一致（不能混用人民币和美元）
        currencies = {line.unit_price.currency for line in self.lines}
        if len(currencies) > 1:
            raise ValueError(f"Order 订单行币种不一致：{currencies}")

    @staticmethod
    def place(order_id: str, buyer_id: str, shipping_address: Address, lines: list[OrderLine]) -> "Order":
        """
        创建订单并直接确认（工厂方法）

        在 MVP 中，MainAgent 已经完成了用户确认，所以创建后直接进入 CONFIRMED。

        参数:
            order_id: 订单号
            buyer_id: 买家 ID
            shipping_address: 收货地址
            lines: 订单行列表

        返回:
            已确认的 Order 实例

        示例:
            >>> order = Order.place("GBX-000001", buyer_id, address, lines)
            >>> order.status  # OrderStatus.CONFIRMED
        """
        order = Order(order_id=order_id, buyer_id=buyer_id, shipping_address=shipping_address, lines=lines)
        order.confirm()  # 直接确认
        return order

    def confirm(self) -> None:
        """
        确认订单

        异常:
            ValueError: 当前状态不是 DRAFT

        注意:
            只能从 DRAFT 转为 CONFIRMED
        """
        if self.status is not OrderStatus.DRAFT:
            raise ValueError(f"仅 DRAFT 态可确认，当前={self.status.value}：{self.order_id}")
        self.status = OrderStatus.CONFIRMED
        self.confirmed_at = _now()

    def cancel(self, reason: str) -> None:
        """
        取消订单

        参数:
            reason: 取消原因（必填）

        异常:
            ValueError: 当前状态不是 CONFIRMED，或 reason 为空

        注意:
            只能从 CONFIRMED 转为 CANCELLED
            取消后需要回补库存（在 UseCase 层处理）
        """
        if self.status is not OrderStatus.CONFIRMED:
            raise ValueError(f"仅 CONFIRMED 态可取消，当前={self.status.value}：{self.order_id}")
        if not reason or not reason.strip():
            raise ValueError("Order.cancel 必须提供 reason")
        self.status = OrderStatus.CANCELLED
        self.cancelled_at = _now()
        self.cancel_reason = reason

    def total_amount(self) -> Money:
        """
        计算订单总金额

        返回:
            订单总金额（所有订单行的小计之和）

        示例:
            >>> order.total_amount()  # Money(8900, "CNY") 表示 89 元
        """
        total = self.lines[0].subtotal()
        for line in self.lines[1:]:
            total = total.add(line.subtotal())
        return total

    def snapshot(self) -> dict:
        """
        生成订单快照（给工具层回传）

        返回:
            订单的结构化字典

        安全设计:
            金额只出现在这里，Agent 不允许自行计算
            防止模型"编造"价格

        示例:
            >>> order.snapshot()
            {
                "order_id": "GBX-000001",
                "status": "CONFIRMED",
                "total_amount_major": 89.0,
                "currency": "CNY",
                ...
            }
        """
        return {
            "order_id": self.order_id,
            "buyer_id": self.buyer_id,
            "status": self.status.value,
            "total_amount_major": self.total_amount().to_major_units(),
            "currency": self.total_amount().currency,
            "shipping_address": self.shipping_address.one_line(),
            "lines": [
                {
                    "product_id": line.product_id,
                    "sku_id": line.sku_id,
                    "title": line.title,
                    "unit_price_major": line.unit_price.to_major_units(),
                    "quantity": line.quantity,
                }
                for line in self.lines
            ],
            "created_at": self.created_at.isoformat(),
            "cancel_reason": self.cancel_reason,
        }

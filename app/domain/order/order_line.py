# -*- coding: utf-8 -*-
"""
OrderLine 订单行（Entity）

===========================================
什么是订单行？
===========================================
一个订单可以包含多个商品，每个商品就是一条"订单行"。

举例：订单 GBX-000001 包含：
- 订单行 1：露营灯 × 1，单价 89 元
- 订单行 2：登山杖 × 2，单价 199 元

===========================================
为什么 unit_price 要做"快照"？
===========================================
防止商品调价影响已下的订单：
- 下单时商品价格是 89 元
- 第二天商家调价为 99 元
- 但你的订单还是按 89 元结算（快照保存了下单时的价格）

这是电商系统的基本规则：**订单价格以下单时刻为准**。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.domain.catalog.money import Money


@dataclass(frozen=True)  # 不可变，创建后不能修改（保护快照）
class OrderLine:
    """
    订单行

    代表订单中的一个商品项。

    属性:
        product_id: 商品 ID（如 "P1008"）
        sku_id: SKU ID（如 "P1008-S1"）
        title: 商品标题（快照，避免商品改名影响）
        unit_price: 单价（快照，避免调价影响）
        quantity: 数量（必须为正整数）

    不变量:
        - quantity 必须为正整数
    """

    product_id: str      # 商品 ID
    sku_id: str          # SKU ID
    title: str           # 商品标题（快照）
    unit_price: Money    # 单价（快照）
    quantity: int        # 数量

    def __post_init__(self) -> None:
        """
        创建后的验证（不变量检查）

        确保:
        - quantity 必须为正整数

        异常:
            ValueError: 数量不合法
        """
        if self.quantity <= 0:
            raise ValueError(f"OrderLine.quantity 必须为正整数：{self.sku_id}")

    def subtotal(self) -> Money:
        """
        计算小计金额

        返回:
            单价 × 数量

        示例:
            >>> line = OrderLine(..., unit_price=Money.from_major_units(89, "CNY"), quantity=2)
            >>> line.subtotal()  # Money(17800, "CNY") = 178 元
        """
        return self.unit_price.multiply(self.quantity)

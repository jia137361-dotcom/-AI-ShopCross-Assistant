# -*- coding: utf-8 -*-
"""
SKU 实体（Entity）

===========================================
什么是 SKU？
===========================================
SKU（Stock Keeping Unit，库存量单位）是最小的可售卖单元。

举例：
- Product（SPU）：iPhone 15
- SKU：iPhone 15 黑色 128G、iPhone 15 白色 256G

每个 SKU 有自己的：
- 规格（spec）：颜色、尺寸等
- 价格（price）：不同规格可能不同价
- 库存（stock）：独立管理

===========================================
为什么库存扣减要在这里做？
===========================================
这是"充血模型"（Rich Domain Model）的体现：
- 业务规则（库存不能为负）封装在实体内部
- 外部不能直接修改 stock，必须通过 deduct_stock() / restore_stock()
- 这样保证了数据一致性
"""
from __future__ import annotations

from dataclasses import dataclass

from app.domain.catalog.money import Money


@dataclass
class Sku:
    """
    SKU 实体

    代表一个具体可售卖的商品单元。

    属性:
        sku_id: SKU 唯一标识（如 "P1001-S1"）
        spec: 规格描述（如"黑色 / 20寸"、"军绿色"）
        price: 价格（Money 值对象）
        stock: 库存数量（非负整数）

    不变量:
        - sku_id 不能为空
        - stock 不能为负数
    """

    sku_id: str      # SKU ID
    spec: str        # 规格描述
    price: Money     # 价格
    stock: int       # 库存

    def __post_init__(self) -> None:
        """
        创建后的验证（不变量检查）

        确保:
        1. sku_id 不能为空
        2. stock 不能为负数

        异常:
            ValueError: 验证失败
        """
        if not self.sku_id:
            raise ValueError("Sku.sku_id required")
        if self.stock < 0:
            raise ValueError(f"Sku.stock 必须非负：{self.sku_id}")

    def has_stock(self, quantity: int) -> bool:
        """
        检查库存是否足够

        参数:
            quantity: 需要的数量

        返回:
            True 表示库存足够，False 表示不足

        示例:
            >>> sku.has_stock(5)  # 检查是否有 5 件库存
        """
        return self.stock >= quantity

    def deduct_stock(self, quantity: int) -> None:
        """
        扣减库存（下单时调用）

        参数:
            quantity: 扣减数量

        异常:
            ValueError: 库存不足

        示例:
            >>> sku.deduct_stock(1)  # 下单 1 件，库存减 1

        注意:
            这是一个"写操作"，会修改 stock 字段
            如果下单失败，需要调用 restore_stock() 恢复
        """
        if not self.has_stock(quantity):
            raise ValueError(f"Sku 库存不足：{self.sku_id}，剩余 {self.stock}，需要 {quantity}")
        self.stock -= quantity

    def restore_stock(self, quantity: int) -> None:
        """
        恢复库存（取消订单时调用）

        参数:
            quantity: 恢复数量

        异常:
            ValueError: 恢复数量为负数

        示例:
            >>> sku.restore_stock(1)  # 取消订单，库存加 1

        注意:
            这是 deduct_stock() 的逆操作
        """
        if quantity < 0:
            raise ValueError("restore_stock.quantity 必须非负")
        self.stock += quantity

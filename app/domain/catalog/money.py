# -*- coding: utf-8 -*-
"""
Money 值对象（Value Object）

===========================================
什么是值对象？
===========================================
值对象是领域驱动设计（DDD）中的一个概念，它：
- 没有唯一标识（Identity），只靠值来判断是否相等
- 是不可变的（Immutable），创建后不能修改
- 通常用来表示业务中的"量"，比如金额、长度、时间

===========================================
为什么用"分"存储而不是"元"？
===========================================
计算机处理浮点数（float）会有精度问题：
    0.1 + 0.2 = 0.30000000000000004  # 不精确！

如果用"元"存储价格，计算订单总价时会累积误差。
解决方案：用"分"（最小货币单位）存储为整数（int），计算完再转回"元"显示。

===========================================
不变量（Invariant）
===========================================
不变量是业务规则，必须始终满足：
1. amount_in_minor_units 必须是非负整数（不能欠钱）
2. currency 必须是受支持的 ISO-4217 三位货币代码
"""
from __future__ import annotations

from dataclasses import dataclass


# 支持的货币列表（ISO-4217 标准三位代码）
# USD=美元, EUR=欧元, GBP=英镑, JPY=日元, CNY=人民币
# HKD=港币, AUD=澳元, CAD=加元, SGD=新元
SUPPORTED_CURRENCIES = ("USD", "EUR", "GBP", "JPY", "CNY", "HKD", "AUD", "CAD", "SGD")


@dataclass(frozen=True)  # frozen=True 使实例不可变（创建后不能修改字段）
class Money:
    """
    金额值对象

    属性:
        amount_in_minor_units: 金额（最小货币单位，如"分"）
        currency: 货币代码（如 "CNY"）

    示例:
        >>> price = Money.of(19900, "CNY")  # 199 元
        >>> price.to_major_units()  # 199.0
        >>> str(price)  # "199.00 CNY"
    """

    amount_in_minor_units: int  # 金额（最小单位，如分）
    currency: str               # 货币代码

    @staticmethod
    def of(amount: int, currency: str) -> "Money":
        """
        创建 Money 实例（工厂方法）

        参数:
            amount: 金额（最小货币单位，必须是大于等于 0 的整数）
            currency: 货币代码（如 "CNY"）

        返回:
            Money 实例

        异常:
            ValueError: 金额不是非负整数，或货币代码不受支持

        示例:
            >>> Money.of(19900, "CNY")  # 199 元
            >>> Money.of(-100, "CNY")   # 抛出 ValueError
        """
        # 验证金额类型：必须是 int，不能是 bool（bool 是 int 的子类）
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
            raise ValueError(f"Money.amount_in_minor_units 必须是非负整数，实际={amount}")
        # 验证货币代码：必须在支持列表中
        if currency not in SUPPORTED_CURRENCIES:
            raise ValueError(f"Money.currency 不受支持：{currency}")
        return Money(amount, currency)

    @staticmethod
    def from_major_units(major: float, currency: str) -> "Money":
        """
        从"元"（主单位）创建 Money 实例

        参数:
            major: 金额（主单位，如 199.0 表示 199 元）
            currency: 货币代码

        返回:
            Money 实例

        注意:
            简化处理：所有币种按 100 倍换算（JPY 实际为 1，demo 暂统一）

        示例:
            >>> Money.from_major_units(199.0, "CNY")  # 199 元 = 19900 分
        """
        # 将"元"转换为"分"：乘以 100 后四舍五入取整
        return Money.of(round(major * 100), currency)

    def add(self, other: "Money") -> "Money":
        """
        金额相加

        参数:
            other: 另一个 Money 实例

        返回:
            新的 Money 实例（金额之和）

        异常:
            ValueError: 两个 Money 的货币不同

        示例:
            >>> Money.of(100, "CNY").add(Money.of(200, "CNY"))  # 300 分
        """
        # 先检查货币是否相同（不能把人民币和美元直接相加）
        self._assert_same_currency(other)
        return Money(self.amount_in_minor_units + other.amount_in_minor_units, self.currency)

    def multiply(self, quantity: int) -> "Money":
        """
        金额乘以数量

        参数:
            quantity: 数量（必须是非负整数）

        返回:
            新的 Money 实例（金额 × 数量）

        异常:
            ValueError: 数量不是非负整数

        示例:
            >>> Money.of(19900, "CNY").multiply(3)  # 59700 分（3 件 199 元的商品）
        """
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 0:
            raise ValueError("Money.multiply.quantity 必须是非负整数")
        return Money(self.amount_in_minor_units * quantity, self.currency)

    def to_major_units(self) -> float:
        """
        转换为主单位（元）

        返回:
            金额（主单位，如 199.0）

        示例:
            >>> Money.of(19900, "CNY").to_major_units()  # 199.0
        """
        return self.amount_in_minor_units / 100

    def __str__(self) -> str:
        """
        字符串表示

        返回:
            格式化的金额字符串，如 "199.00 CNY"
        """
        return f"{self.to_major_units():.2f} {self.currency}"

    def _assert_same_currency(self, other: "Money") -> None:
        """
        断言两个 Money 的货币相同（私有方法）

        参数:
            other: 另一个 Money 实例

        异常:
            ValueError: 货币不同

        注意:
            这是一个私有方法（下划线开头），只在内部使用
        """
        if other.currency != self.currency:
            raise ValueError(f"Money 币种不一致：{self.currency} vs {other.currency}")

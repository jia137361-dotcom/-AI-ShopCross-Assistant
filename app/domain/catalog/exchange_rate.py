# -*- coding: utf-8 -*-
"""
ExchangeRateTable 汇率表（Value Object）

===========================================
为什么需要汇率转换？
===========================================
跨境购物涉及多种货币：
- 商品可能以美元标价（如 $219）
- 买家想看人民币价格（如 ¥1555）
- 需要统一货币计算总价

===========================================
为什么用静态快照？
===========================================
MVP（最小可行产品）阶段用硬编码汇率，生产环境替换为汇率服务。
好处：
- 不依赖外部 API，本地可跑
- 替换时只需改 `convert()` 实现，调用方代码不动
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.catalog.money import SUPPORTED_CURRENCIES, Money


# 静态汇率快照：1 单位外币 = N 人民币
# 注意：这是简化版，生产环境应从汇率 API 获取实时数据
_RATES_TO_CNY: dict[str, float] = {
    "CNY": 1.0,    # 人民币对人民币 = 1
    "USD": 7.10,   # 1 美元 = 7.10 人民币
    "EUR": 7.80,   # 1 欧元 = 7.80 人民币
    "GBP": 9.10,   # 1 英镑 = 9.10 人民币
    "JPY": 0.048,  # 1 日元 = 0.048 人民币
    "HKD": 0.91,   # 1 港币 = 0.91 人民币
    "AUD": 4.70,   # 1 澳元 = 4.70 人民币
    "CAD": 5.20,   # 1 加元 = 5.20 人民币
    "SGD": 5.30,   # 1 新元 = 5.30 人民币
}


@dataclass(frozen=True)
class ExchangeRateTable:
    """
    汇率表值对象

    属性:
        rates_to_cny: 各币种对人民币的汇率字典
    """

    rates_to_cny: dict[str, float] = field(default_factory=lambda: dict(_RATES_TO_CNY))

    def rate(self, from_currency: str, to_currency: str) -> float:
        """
        获取两种货币之间的汇率

        参数:
            from_currency: 源货币（如 "USD"）
            to_currency: 目标货币（如 "CNY"）

        返回:
            汇率（1 单位源货币 = N 单位目标货币）

        示例:
            >>> table.rate("USD", "CNY")  # 7.10
            >>> table.rate("CNY", "USD")  # 0.14 (1/7.10)

        计算逻辑:
            通过人民币中转：USD → CNY → EUR
            rate(USD, EUR) = rate(USD, CNY) / rate(EUR, CNY)
                          = 7.10 / 7.80 ≈ 0.91
        """
        for currency in (from_currency, to_currency):
            if currency not in self.rates_to_cny:
                raise ValueError(f"汇率表不支持币种：{currency}")
        return self.rates_to_cny[from_currency] / self.rates_to_cny[to_currency]

    def convert(self, money: Money, to_currency: str) -> Money:
        """
        货币转换

        参数:
            money: 原始金额
            to_currency: 目标货币

        返回:
            转换后的金额

        示例:
            >>> table.convert(Money.from_major_units(100, "USD"), "CNY")
            # Money(71000, "CNY") = 710 元

        注意:
            转换后取整到"分"（最小货币单位）
        """
        if to_currency == money.currency:
            return money  # 相同货币，无需转换
        if to_currency not in SUPPORTED_CURRENCIES:
            raise ValueError(f"Money.currency 不受支持：{to_currency}")
        converted_minor = round(money.amount_in_minor_units * self.rate(money.currency, to_currency))
        return Money.of(converted_minor, to_currency)

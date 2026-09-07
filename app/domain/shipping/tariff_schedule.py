# -*- coding: utf-8 -*-
"""
TariffSchedule 关税运费规则（Domain Service）

===========================================
什么是到手价？（Landed Price）
===========================================
跨境购物的真实成本不是商品标价，而是"到手价"：

    到手价 = 商品小计 + 国际运费 + 关税

举例：
- 商品标价：$219（降噪耳机）
- 运费：$9.15
- 关税：$0（美国 800 美元以下免税）
- 到手价：$228.15

===========================================
什么是免税额度？（De Minimis）
===========================================
多数国家设有免税额度，小计低于额度时关税为 0：
- 美国：约 800 美元
- 欧盟：约 150 欧元
- 中国：约 5000 元人民币

===========================================
为什么是"纯规则纯函数"？
===========================================
- 给定相同的输入，总是返回相同的输出
- 不依赖外部状态（数据库、网络）
- 好处：易于单测，生产替换为关税服务时只需换实现
"""
from __future__ import annotations

from dataclasses import dataclass

from app.domain.catalog.exchange_rate import ExchangeRateTable
from app.domain.catalog.money import Money


# ============================================
# 关税费率表（国家 → 品类 → 费率）
# ============================================
# 未列出的品类走 "*" 兜底
_TARIFF_RATES: dict[str, dict[str, float]] = {
    "CN": {"数码配件": 0.13, "旅行装备": 0.09, "户外运动": 0.09, "家居生活": 0.09, "*": 0.09},
    "US": {"数码配件": 0.0, "旅行装备": 0.075, "户外运动": 0.075, "家居生活": 0.05, "*": 0.06},
    "EU": {"*": 0.12},
    "JP": {"*": 0.08},
    "SG": {"*": 0.07},
}

# ============================================
# 免税额度（单位：人民币分）
# ============================================
_DE_MINIMIS_CNY_MINOR: dict[str, int] = {
    "CN": 5_000_00,   # 5000 元（个人物品行邮口径，简化）
    "US": 800 * 710,  # 800 USD 折 CNY 分
    "EU": 150 * 780,  # 150 EUR 折 CNY 分
    "JP": 10_000 * 5, # 简化口径
    "SG": 400 * 530,  # 400 SGD 折 CNY 分
}

# ============================================
# 基础运费（单位：人民币分，单件）
# ============================================
# 多件按 60% 递增（简化的续重逻辑）
_BASE_FREIGHT_CNY_MINOR: dict[str, int] = {
    "CN": 25_00,   # 25 元
    "US": 65_00,   # 65 元
    "EU": 75_00,   # 75 元
    "JP": 45_00,   # 45 元
    "SG": 40_00,   # 40 元
}


@dataclass(frozen=True)
class ShippingQuote:
    """
    运费关税报价（值对象）

    包含到手价的三要素：小计、运费、关税。

    属性:
        ship_to: 目的国
        subtotal: 商品小计（目标币种）
        freight: 运费
        tariff: 关税
        tariff_rate: 关税费率
        de_minimis_applied: 是否命中免税额度
    """

    ship_to: str                    # 目的国
    subtotal: Money                 # 商品小计
    freight: Money                  # 运费
    tariff: Money                   # 关税
    tariff_rate: float              # 关税费率
    de_minimis_applied: bool        # 是否免税

    def landed_total(self) -> Money:
        """
        计算到手总价

        返回:
            小计 + 运费 + 关税
        """
        return self.subtotal.add(self.freight).add(self.tariff)

    def to_dict(self) -> dict:
        """
        转换为字典（给前端展示）

        返回:
            包含所有价格明细的字典
        """
        return {
            "ship_to": self.ship_to,
            "subtotal_major": self.subtotal.to_major_units(),
            "freight_major": self.freight.to_major_units(),
            "tariff_major": self.tariff.to_major_units(),
            "tariff_rate": self.tariff_rate,
            "de_minimis_applied": self.de_minimis_applied,
            "landed_total_major": self.landed_total().to_major_units(),
            "currency": self.subtotal.currency,
        }


@dataclass(frozen=True)
class TariffSchedule:
    """
    关税运费规则（领域服务）

    根据目的国和品类计算关税和运费。
    """

    rates: ExchangeRateTable  # 汇率表

    def supported_destinations(self) -> list[str]:
        """
        获取支持的目的地列表

        返回:
            支持的国家代码列表
        """
        return sorted(_TARIFF_RATES.keys())

    def quote(self, subtotal: Money, category: str, ship_to: str, quantity: int, target_currency: str) -> ShippingQuote:
        """
        计算到手价

        参数:
            subtotal: 商品小计
            category: 品类（如"数码配件"）
            ship_to: 目的国（如"US"）
            quantity: 数量
            target_currency: 目标币种（如"USD"）

        返回:
            ShippingQuote 报价

        计算逻辑:
            1. 运费 = 首件全价 + 续件 × 60%
            2. 关税 = max(0, 小计 - 免税额度) × 费率
            3. 到手价 = 小计 + 运费 + 关税

        示例:
            >>> schedule.quote(
            ...     subtotal=Money.from_major_units(219, "USD"),
            ...     category="数码配件",
            ...     ship_to="US",
            ...     quantity=1,
            ...     target_currency="USD"
            ... )
            # ShippingQuote(subtotal=$219, freight=$9.15, tariff=$0)
        """
        # 验证目的国
        if ship_to not in _TARIFF_RATES:
            raise ValueError(f"暂不支持的目的国：{ship_to}（支持 {self.supported_destinations()}）")
        if quantity <= 0:
            raise ValueError("quantity 必须为正整数")

        # 转换为目标币种
        subtotal_target = self.rates.convert(subtotal, target_currency)

        # ============================================
        # 计算运费：首件全价 + 续件 60%
        # ============================================
        base_freight_cny = Money.of(_BASE_FREIGHT_CNY_MINOR[ship_to], "CNY")
        freight_minor_cny = round(base_freight_cny.amount_in_minor_units * (1 + 0.6 * (quantity - 1)))
        freight_target = self.rates.convert(Money.of(freight_minor_cny, "CNY"), target_currency)

        # ============================================
        # 计算关税：超出免税额度部分 × 费率
        # ============================================
        rate_table = _TARIFF_RATES[ship_to]
        tariff_rate = rate_table.get(category, rate_table["*"])  # 未列品类走 "*"
        subtotal_cny = self.rates.convert(subtotal, "CNY")
        de_minimis_minor = _DE_MINIMIS_CNY_MINOR[ship_to]
        taxable_minor_cny = max(0, subtotal_cny.amount_in_minor_units - de_minimis_minor)
        de_minimis_applied = taxable_minor_cny == 0  # 是否免税
        tariff_cny = Money.of(round(taxable_minor_cny * tariff_rate), "CNY")
        tariff_target = self.rates.convert(tariff_cny, target_currency)

        return ShippingQuote(
            ship_to=ship_to,
            subtotal=subtotal_target,
            freight=freight_target,
            tariff=tariff_target,
            tariff_rate=tariff_rate,
            de_minimis_applied=de_minimis_applied,
        )

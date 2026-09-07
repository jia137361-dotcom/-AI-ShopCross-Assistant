# -*- coding: utf-8 -*-
"""
ProductSearchSpec 检索规格（Value Object）

===========================================
什么是检索规格？
===========================================
买家说"帮我找一个 300 块以内的露营灯"，SearchAgent 需要把它改写成标准化的检索规格：

    normalized_query: "露营灯 户外 照明"  （去掉语气词，保留关键属性）
    price_max_major: 300.0               （价格上限）
    category: "户外运动"                  （品类槽位）
    ship_to: "CN"                        （收货地）

===========================================
什么是槽位（Slot）？
===========================================
槽位是从用户自然语言中提取的结构化信息：
- category: 品类（如"旅行装备"）
- ship_to: 收货地（如"US"）
- price_max_major: 价格上限（如 300.0）

===========================================
为什么硬约束不交给 embedding/reranker？
===========================================
向量检索擅长"相似度"匹配，但不擅长精确数值比较。
比如"300 块以内"，向量检索无法精确判断 299 和 301 的区别。

所以硬约束（价格、收货地）在 UseCase 层做结构化过滤。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ProductSearchSpec:
    """
    检索规格值对象

    把买家自然语言 query 改写为标准化的检索参数。

    属性:
        normalized_query: 标准化检索词（保留品类词与关键属性词）
        category: 品类槽位（可选，如"旅行装备"）
        ship_to: 收货国家（可选，如"US"）
        locale: 语言区域（默认 zh-CN）
        top_k: 返回候选数量（默认 5）
        target_currency: 价格口径币种（默认 CNY）
        price_max_major: 价格上限（可选，目标币种主单位）

    不变量:
        - normalized_query 不能为空
        - top_k 必须为正整数
    """

    normalized_query: str                    # 标准化检索词
    category: Optional[str] = None           # 品类槽位
    ship_to: Optional[str] = None            # 收货国家
    locale: str = "zh-CN"                    # 语言区域
    top_k: int = 5                           # 返回候选数量
    target_currency: str = "CNY"             # 价格口径币种
    price_max_major: Optional[float] = None  # 价格上限

    def __post_init__(self) -> None:
        """
        创建后的验证（不变量检查）

        确保:
        - normalized_query 不能为空
        - top_k 必须为正整数

        异常:
            ValueError: 验证失败
        """
        if not self.normalized_query or not self.normalized_query.strip():
            raise ValueError("ProductSearchSpec.normalized_query required")
        if self.top_k <= 0:
            raise ValueError("ProductSearchSpec.top_k 必须为正整数")

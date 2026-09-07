# -*- coding: utf-8 -*-
"""
Product 聚合根（Aggregate Root）

===========================================
什么是聚合根？
===========================================
聚合根是领域驱动设计（DDD）中的核心概念：
- 它是一个"入口"，外部只能通过聚合根来访问内部对象
- 它负责维护业务不变量（Invariant）
- 它有自己的生命周期（创建、修改、删除）

===========================================
SPU 和 SKU 的区别？
===========================================
- SPU（Standard Product Unit，标准产品单位）：一个"商品"
  比如"iPhone 15"是一个 SPU
- SKU（Stock Keeping Unit，库存量单位）：一个"具体可售卖单元"
  比如"iPhone 15 黑色 128G"是一个 SKU

一个 Product（SPU）可以有多个 SKU，每个 SKU 有自己的价格和库存。

===========================================
为什么 searchable_text() 很重要？
===========================================
这个方法把商品的所有可检索信息拼成一个长字符串，用于：
1. BM25 关键词匹配
2. Embedding 向量化

所以描述写得越好，检索命中率越高。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.domain.catalog.sku import Sku


@dataclass(frozen=True)
class ProductHighlight:
    """
    商品亮点（值对象）

    用于展示在商品卡片上的卖点，如"防水"、"续航 72 小时"。

    属性:
        label: 亮点名称（如"防水"）
        detail: 亮点详情（如"IPX5 防水"）
    """
    label: str        # 亮点名称
    detail: str = ""  # 亮点详情（可选）


@dataclass
class Product:
    """
    商品聚合根

    代表一个 SPU（标准产品单位），包含基本信息和多个 SKU。

    属性:
        product_id: 商品唯一标识（如 "P1001"）
        title: 商品标题（如"Nomadica 旅行三件套"）
        brand: 品牌（如"Nomadica"）
        category: 品类（如"旅行装备"）
        origin_country: 原产国（如"VN"表示越南）
        description: 商品描述
        image_url: 商品图片 URL
        highlights: 商品亮点列表
        ships_to: 可配送国家列表（如 ["CN", "US", "SG"]）
        skus: SKU 列表（至少一个）

    不变量:
        - product_id 不能为空
        - 至少要有一个 SKU
    """

    product_id: str                                    # 商品 ID
    title: str                                         # 商品标题
    brand: str                                         # 品牌
    category: str                                      # 品类
    origin_country: str                                # 原产国
    description: str                                   # 商品描述
    image_url: str = ""                                # 图片 URL
    highlights: list[ProductHighlight] = field(default_factory=list)  # 亮点列表
    ships_to: list[str] = field(default_factory=list)  # 可配送国家
    skus: list[Sku] = field(default_factory=list)      # SKU 列表

    def __post_init__(self) -> None:
        """
        创建后的验证（不变量检查）

        确保:
        1. product_id 不能为空
        2. 至少要有一个 SKU

        异常:
            ValueError: 验证失败
        """
        if not self.product_id:
            raise ValueError("Product.product_id required")
        if not self.skus:
            raise ValueError(f"Product 至少要有一个 Sku：{self.product_id}")

    def primary_sku(self) -> Sku:
        """
        获取主 SKU（第一个 SKU）

        返回:
            第一个 SKU

        注意:
            用于商品卡上展示默认价格

        示例:
            >>> product.primary_sku().price  # 默认价格
        """
        return self.skus[0]

    def find_sku(self, sku_id: str) -> Optional[Sku]:
        """
        根据 SKU ID 查找 SKU

        参数:
            sku_id: SKU 唯一标识（如 "P1001-S1"）

        返回:
            找到的 SKU，未找到返回 None

        示例:
            >>> product.find_sku("P1001-S1")  # 返回 SKU 或 None
        """
        return next((s for s in self.skus if s.sku_id == sku_id), None)

    def searchable_text(self) -> str:
        """
        生成可检索文本（用于 BM25 和 Embedding）

        把商品的所有可检索信息拼成一个长字符串：
        - 标题（权重最高，因为最精准）
        - 品牌
        - 品类
        - 原产国
        - 描述
        - 亮点

        返回:
            拼接后的检索文本

        示例:
            >>> product.searchable_text()
            'Nomadica 旅行三件套 Nomadica 旅行装备 VN 帆布加尼龙材质...'

        注意:
            这个文本质量直接影响检索命中率！
        """
        # 把亮点列表拼成字符串
        highlight_text = " ".join(f"{h.label} {h.detail}" for h in self.highlights)
        # 把所有字段拼成一个长字符串
        return " ".join(
            [self.title, self.brand, self.category, self.origin_country, self.description, highlight_text],
        )

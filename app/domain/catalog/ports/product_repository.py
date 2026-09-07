# -*- coding: utf-8 -*-
"""
ProductRepository 商品仓储端口（Port）

===========================================
什么是仓储（Repository）？
===========================================
仓储是领域层定义的"数据访问接口"，它：
- 不关心数据存在哪里（内存、数据库、文件）
- 只定义"能做什么"（查找、保存、删除）
- 由 infrastructure 层提供具体实现

===========================================
为什么 domain 不关心实现？
===========================================
这是洋葱架构的核心原则：
- domain 层只关注业务规则
- infrastructure 层关注技术细节（SQL、文件 IO）
- 切换数据库（SQLite → PostgreSQL）不需要改业务代码

===========================================
两套实现
===========================================
- InMemoryProductRepository: 基于内存字典（本地开发、测试）
- 生产环境可换为 SqlProductRepository（PostgreSQL/MySQL）
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from app.domain.catalog.product import Product


class ProductRepository(ABC):
    """
    商品仓储接口（抽象基类）

    定义了商品数据访问的标准操作。
    """

    @abstractmethod
    async def find_by_id(self, product_id: str) -> Optional[Product]:
        """
        根据 ID 查找单个商品

        参数:
            product_id: 商品 ID（如 "P1001"）

        返回:
            找到的商品，未找到返回 None

        示例:
            >>> product = await repo.find_by_id("P1001")
        """
        ...

    @abstractmethod
    async def find_by_ids(self, product_ids: list[str]) -> list[Product]:
        """
        根据 ID 列表批量查找商品

        参数:
            product_ids: 商品 ID 列表

        返回:
            找到的商品列表（不保证顺序，可能少于输入数量）

        示例:
            >>> products = await repo.find_by_ids(["P1001", "P1002"])
        """
        ...

    @abstractmethod
    async def list_all(self) -> list[Product]:
        """
        获取所有商品

        返回:
            所有商品的列表

        注意:
            数据量大时慎用（可能内存溢出）

        示例:
            >>> all_products = await repo.list_all()
        """
        ...

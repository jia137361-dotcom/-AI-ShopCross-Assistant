# -*- coding: utf-8 -*-
"""
QdrantProductIndex 商品向量索引（Qdrant 实现）

===========================================
什么是 Qdrant？
===========================================
Qdrant 是一个**向量数据库**，专门用于存储和检索向量。

核心概念：
- Collection（集合）：类似 SQL 中的"表"，存储一类向量
- Point（点）：一条向量记录，包含 id、vector、payload
- Payload（载荷）：附加的元数据（如 product_id）
- Distance（距离度量）：COSINE（余弦相似度）、EUCLID（欧氏距离）、DOT（点积）

===========================================
两种运行模式
===========================================
1. **服务端模式**（QDRANT_URL 已配置）：
   - 连接 Qdrant 服务端（Docker 容器或远程服务器）
   - 支持多进程/多实例共享
   - 生产环境推荐

2. **本地嵌入模式**（QDRANT_URL 未配置）：
   - qdrant-client 内置的本地模式
   - 数据落盘到 DATA_DIR/qdrant
   - 零外部依赖，适合本地开发
   - 注意：单进程文件锁，多实例会冲突

===========================================
为什么用 UUID5 作为 point id？
===========================================
- 确定性：同一个 product_id 总是生成相同的 UUID
- 幂等：重复 upsert 同一条数据不会创建重复记录
- 格式：符合 Qdrant 的 UUID 格式要求

===========================================
什么是 upsert？
===========================================
upsert = update + insert
- 如果记录已存在，更新它
- 如果记录不存在，插入新记录

好处：可以重复调用，不会创建重复数据。
"""
from __future__ import annotations

import uuid

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from app.domain.catalog.ports.retrieval_ports import ProductVectorIndex, VectorHit
from app.domain.catalog.product import Product
from app.infrastructure.settings import Settings


def _point_id(product_id: str) -> str:
    """
    生成确定性的 UUID5 作为 Qdrant point id

    参数:
        product_id: 商品 ID（如 "P1001"）

    返回:
        UUID 字符串（如 "a1b2c3d4-e5f6-7890-abcd-ef1234567890"）

    注意:
        UUID5 是确定性的，同一个 product_id 总是生成相同的 UUID
        这保证了 upsert 的幂等性

    示例:
        >>> _point_id("P1001")
        'a1b2c3d4-e5f6-7890-abcd-ef1234567890'  # 总是相同
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"shopcross/product/{product_id}"))


class QdrantProductIndex(ProductVectorIndex):
    """
    商品向量索引的 Qdrant 实现

    实现了 ProductVectorIndex 接口，提供向量存储和检索功能。

    属性:
        _client: Qdrant 异步客户端
        _collection: 集合名称
    """

    def __init__(self, settings: Settings) -> None:
        """
        初始化 Qdrant 客户端

        参数:
            settings: 应用配置

        根据 QDRANT_URL 是否配置，选择服务端模式或本地模式：
        - 已配置：连接 Qdrant 服务端
        - 未配置：使用本地嵌入模式（落盘 DATA_DIR/qdrant）
        """
        if settings.qdrant_url:
            # 服务端模式：连接 Qdrant 服务端（Docker / 远程）
            self._client = AsyncQdrantClient(url=settings.qdrant_url)
        else:
            # 本地嵌入模式：数据落盘到本地目录
            local_path = settings.data_dir / "qdrant"
            local_path.parent.mkdir(parents=True, exist_ok=True)
            self._client = AsyncQdrantClient(path=str(local_path))
        self._collection = settings.qdrant_collection

    async def ensure_ready(self, vector_dim: int) -> None:
        """
        确保集合存在（幂等操作）

        参数:
            vector_dim: 向量维度（如 1024）

        注意:
            - 如果集合已存在，不会重复创建
            - 使用 COSINE（余弦相似度）作为距离度量

        COSINE 距离:
            - 值域：[-1, 1]
            - 1 表示完全相同
            - 0 表示正交（无关）
            - -1 表示完全相反
        """
        if not await self._client.collection_exists(self._collection):
            await self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(size=vector_dim, distance=Distance.COSINE),
            )

    async def upsert_products(self, products: list[Product], embeddings: list[list[float]]) -> None:
        """
        批量写入商品向量

        参数:
            products: 商品列表
            embeddings: 对应的向量列表

        注意:
            - products 和 embeddings 数量必须一致
            - 使用 UUID5 作为 point id，保证幂等性
            - payload 只存 product_id（节省存储空间）

        示例:
            >>> products = [product1, product2]
            >>> embeddings = [[0.1, 0.2, ...], [0.3, 0.4, ...]]
            >>> await index.upsert_products(products, embeddings)
        """
        if len(products) != len(embeddings):
            raise ValueError("products 与 embeddings 数量不一致")
        if not products:
            return
        # 构建 Point 列表
        points = [
            PointStruct(
                id=_point_id(product.product_id),  # 确定性 UUID
                vector=embedding,                   # 向量
                payload={"product_id": product.product_id},  # 元数据
            )
            for product, embedding in zip(products, embeddings)
        ]
        # 批量 upsert
        await self._client.upsert(collection_name=self._collection, points=points)

    async def search(self, embedding: list[float], top_n: int) -> list[VectorHit]:
        """
        相似度检索

        参数:
            embedding: 查询向量
            top_n: 返回前 N 个最相似的商品

        返回:
            检索结果列表（按相似度降序）

        示例:
            >>> query_vector = [0.1, 0.2, ...]  # 1024 维
            >>> hits = await index.search(query_vector, top_n=10)
            >>> hits[0].product_id  # 最相似的商品 ID
            >>> hits[0].score       # 相似度分数（COSINE，越高越相似）
        """
        result = await self._client.query_points(
            collection_name=self._collection,
            query=embedding,
            limit=top_n,
            with_payload=True,  # 返回 payload（包含 product_id）
        )
        return [
            VectorHit(product_id=point.payload["product_id"], score=point.score)
            for point in result.points
            if point.payload and "product_id" in point.payload
        ]

    async def close(self) -> None:
        """
        关闭 Qdrant 客户端连接

        注意:
            应用关闭时调用，释放资源
        """
        await self._client.close()

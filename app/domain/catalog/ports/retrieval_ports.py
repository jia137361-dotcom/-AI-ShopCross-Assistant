# -*- coding: utf-8 -*-
"""
检索基础设施端口（Ports）

===========================================
什么是端口（Port）？
===========================================
端口是领域层定义的"技术能力接口"，它：
- 不关心具体用什么技术（OpenAI、BGE、Qdrant、Milvus）
- 只定义"能做什么"（生成向量、搜索向量、精排）
- 由 infrastructure 层提供具体实现

===========================================
三个端口的作用
===========================================
1. EmbeddingClient: 把文本变成向量
   - 输入："旅行三件套"
   - 输出：[0.12, 0.34, 0.56, ...]（1024 维）

2. ProductVectorIndex: 存储和检索向量
   - 存储：把商品向量写入 Qdrant
   - 检索：找最相似的 top_n 个商品

3. Reranker: 精确排序
   - 输入：查询 + 候选文档
   - 输出：每个文档的相关性分数

===========================================
为什么任一环节不可用要降级？
===========================================
这是韧性工程（Resilience）的体现：
- Embedding 服务挂了 → 降级到关键词召回
- Reranker 挂了 → 按向量分排序（跳过精排）
- Qdrant 挂了 → 降级到关键词召回

系统不会因为单个服务故障而完全不可用。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.domain.catalog.product import Product


class EmbeddingClient(ABC):
    """
    Embedding 客户端接口

    把文本转换为向量（一串数字）。
    """

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """
        单条文本向量化

        参数:
            text: 输入文本

        返回:
            向量（浮点数列表）

        示例:
            >>> vector = await client.embed("旅行三件套")
            >>> len(vector)  # 1024（维度）
        """
        ...

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        批量文本向量化

        参数:
            texts: 输入文本列表

        返回:
            向量列表（与输入等长）

        示例:
            >>> vectors = await client.embed_batch(["旅行三件套", "露营灯"])
            >>> len(vectors)  # 2
        """
        ...


@dataclass(frozen=True)
class VectorHit:
    """
    向量检索结果（值对象）

    属性:
        product_id: 商品 ID
        score: 相似度分数（越高越相似）
    """
    product_id: str  # 商品 ID
    score: float     # 相似度分数


class ProductVectorIndex(ABC):
    """
    商品向量索引接口

    存储商品向量，支持相似度检索。
    """

    @abstractmethod
    async def ensure_ready(self, vector_dim: int) -> None:
        """
        确保索引就绪（幂等操作）

        参数:
            vector_dim: 向量维度（如 1024）

        注意:
            如果索引已存在，不会重复创建（幂等）
        """
        """确保 collection 存在（幂等）。"""
        ...

    @abstractmethod
    async def upsert_products(self, products: list[Product], embeddings: list[list[float]]) -> None:
        """
        写入商品向量

        参数:
            products: 商品列表
            embeddings: 对应的向量列表

        注意:
            如果商品已存在，会更新（upsert = update + insert）
        """
        ...

    @abstractmethod
    async def search(self, embedding: list[float], top_n: int) -> list[VectorHit]:
        """
        相似度检索

        参数:
            embedding: 查询向量
            top_n: 返回前 N 个最相似的商品

        返回:
            检索结果列表（按相似度降序）

        示例:
            >>> hits = await index.search(query_vector, top_n=10)
            >>> hits[0].product_id  # 最相似的商品 ID
            >>> hits[0].score       # 相似度分数
        """
        ...


class Reranker(ABC):
    """
    重排器接口

    对召回的候选文档进行精确排序。
    """

    @abstractmethod
    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        """
        精排

        参数:
            query: 查询文本
            documents: 候选文档列表

        返回:
            每个文档的相关性分数（与 documents 等长）

        异常:
            失败时抛出异常，由调用方降级为按向量分排序

        示例:
            >>> scores = await reranker.rerank("露营灯", ["商品1描述", "商品2描述"])
            >>> len(scores)  # 2
        """
        ...

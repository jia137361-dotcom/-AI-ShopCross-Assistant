# -*- coding: utf-8 -*-
"""
index_bootstrap 向量索引引导（Bootstrap）

===========================================
什么是 Bootstrap？
===========================================
Bootstrap 是"引导"的意思，指应用启动时的初始化操作。

这里的作用：
- 启动时读取所有商品
- 批量生成 Embedding
- 写入 Qdrant 向量库

===========================================
为什么幂等？
===========================================
- UUID5 作为 point id（确定性）
- upsert 语义（重复写入不创建重复记录）
- 可以多次运行，不会出问题

===========================================
为什么失败不阻塞启动？
===========================================
- 向量检索是锦上添花，不是必须的
- 失败时检索链路自动降级到关键词召回
- 服务仍然可用，只是检索质量下降
"""
from __future__ import annotations

import logging

from app.domain.catalog.ports.product_repository import ProductRepository
from app.domain.catalog.ports.retrieval_ports import EmbeddingClient, ProductVectorIndex

logger = logging.getLogger(__name__)


async def bootstrap_product_index(
    product_repo: ProductRepository,
    embedder: EmbeddingClient,
    vector_index: ProductVectorIndex,
) -> bool:
    """
    构建向量索引（启动时调用）

    参数:
        product_repo: 商品仓库
        embedder: Embedding 客户端
        vector_index: 向量索引

    返回:
        True 表示成功，False 表示失败（检索走关键词降级）

    流程:
        1. 读取所有商品
        2. 批量生成 Embedding
        3. 确保集合存在
        4. 批量写入 Qdrant

    注意:
        - 建库失败只告警，不阻塞启动
        - 检索链路会自动降级到关键词召回

    示例:
        >>> success = await bootstrap_product_index(repo, embedder, index)
        >>> if not success:
        ...     print("向量检索不可用，将使用关键词召回")
    """
    try:
        # 1. 读取所有商品
        products = await product_repo.list_all()
        # 2. 批量生成 Embedding
        embeddings = await embedder.embed_batch([p.searchable_text() for p in products])
        if not embeddings:
            logger.warning("商品库为空，跳过向量建库")
            return False
        # 3. 确保集合存在
        await vector_index.ensure_ready(vector_dim=len(embeddings[0]))
        # 4. 批量写入 Qdrant
        await vector_index.upsert_products(products, embeddings)
        logger.info("向量索引就绪：%d 个商品（dim=%d）", len(products), len(embeddings[0]))
        return True
    except Exception as err:
        # 建库失败不阻塞启动，检索链路会自动降级
        logger.warning("向量建库失败，检索将降级关键词召回：%s", err)
        return False

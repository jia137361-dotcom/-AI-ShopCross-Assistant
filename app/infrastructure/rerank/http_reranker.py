# -*- coding: utf-8 -*-
"""
HttpReranker HTTP 精排客户端（HTTP Reranker）

===========================================
什么是 Reranker？
===========================================
Reranker 是精排模型，对召回的候选文档逐一打分，重新排序。

为什么需要 Reranker？
- Embedding 检索是"双塔模型"：查询和文档分别编码，速度快但精度有限
- Reranker 是"交叉模型"：查询和文档一起输入，精度更高但速度慢
- 所以先用 Embedding 快速召回一批，再用 Reranker 精排

===========================================
两种协议
===========================================
不同 Reranker 服务的 API 格式不同：

1. **Qwen3.7-Reranker**（百炼）:
   - URL: /api/v1/services/rerank/text-rerank/text-rerank
   - 请求: {"model": ..., "input": {"query": ..., "documents": ...}}
   - 响应: {"output": {"results": [{"index": 0, "relevance_score": 0.95}]}}

2. **通用协议**（Jina/TEI/vLLM）:
   - URL: /rerank
   - 请求: {"model": ..., "query": ..., "documents": ...}
   - 响应: {"results": [{"index": 0, "score": 0.95}]}

代码兼容两种协议。

===========================================
失败处理
===========================================
- RERANKER_BASE_URL 未配置 → 不实例化本类
- 调用失败抛异常 → CatalogSearchUseCase 降级为按向量分排序
- 标注 rerank_applied=false → 让调用方知道没有精排
"""
from __future__ import annotations

import httpx

from app.domain.catalog.ports.retrieval_ports import Reranker
from app.infrastructure.settings import Settings


class HttpReranker(Reranker):
    """
    HTTP 精排客户端

    实现了 Reranker 接口，调用 HTTP 精排服务。

    属性:
        _base_url: API 基础 URL
        _model: 模型名称
        _api_key: API 密钥
        _timeout: 请求超时时间
    """

    def __init__(self, settings: Settings, timeout_seconds: float = 3.0) -> None:
        """
        初始化精排客户端

        参数:
            settings: 应用配置
            timeout_seconds: 请求超时时间（默认 3 秒）

        注意:
            百炼 Reranker 与 Embedding 使用同一百炼 Key
            聊天模型可能来自不同供应商
        """
        self._base_url = settings.reranker_base_url.rstrip("/")
        self._model = settings.reranker_model
        self._api_key = settings.embedding_api_key
        self._timeout = timeout_seconds

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        """
        精排

        参数:
            query: 查询文本
            documents: 候选文档列表

        返回:
            每个文档的相关性分数（与 documents 等长）

        异常:
            RuntimeError: 响应格式异常

        实现:
            1. 根据模型名判断协议类型
            2. 构造请求
            3. 调用 API
            4. 解析响应

        示例:
            >>> scores = await reranker.rerank("露营灯", ["商品1描述", "商品2描述"])
            >>> len(scores)  # 2
        """
        if not documents:
            return []
        # 判断是否为 Qwen3.7-Reranker（协议不同）
        is_qwen37 = self._model == "qwen3.7-text-rerank"
        url = (
            f"{self._base_url}/api/v1/services/rerank/text-rerank/text-rerank"
            if is_qwen37
            else f"{self._base_url}/rerank"
        )
        payload = (
            {
                "model": self._model,
                "input": {"query": query, "documents": documents},
                "parameters": {
                    "top_n": len(documents),
                    "return_documents": False,
                    "instruct": "Retrieve semantically similar products.",
                },
            }
            if is_qwen37
            else {"model": self._model, "query": query, "documents": documents}
        )
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        # 兼容两种响应格式
        results = body.get("output", {}).get("results") if is_qwen37 else body.get("results")
        if not isinstance(results, list) or len(results) != len(documents):
            raise RuntimeError(f"rerank 响应异常：{str(body)[:200]}")
        # 按 index 回位
        scores = [0.0] * len(documents)
        for item in results:
            scores[item["index"]] = float(item.get("relevance_score", item.get("score", 0.0)))
        return scores

# -*- coding: utf-8 -*-
"""
OpenAIEmbeddingClient Embedding 客户端（OpenAI 兼容接口）

===========================================
什么是 Embedding？
===========================================
Embedding 是把文本变成一串数字（向量），使得语义相近的文本，其向量也相近。

比如：
- "露营灯" → [0.12, 0.34, 0.56, ...]（1024 维）
- "户外照明" → [0.15, 0.31, 0.59, ...]（与"露营灯"相近）
- "蓝牙耳机" → [0.89, 0.02, 0.11, ...]（与"露营灯"很远）

===========================================
为什么用 httpx 直连而不是 openai SDK？
===========================================
- openai SDK 的 embedding 封装不够灵活
- httpx 直连可以对接**任意 OpenAI 兼容网关**（百炼、SiliconFlow、vLLM 等）
- 代码更清晰，容易调试

===========================================
批量上限的坑（重要！）
===========================================
实测发现：某些 OpenAI 兼容网关在单次请求超过 10 条文本时：
- 返回 HTTP 200 + content-type: application/json + **空 body**
- `raise_for_status()` 因为状态码是 200 而放行
- 最终在 `response.json()` 处抛出 `JSONDecodeError`

更隐蔽的是：
- 商品种子库原本恰好 10 个 SPU，正好卡在上限内，问题一直没暴露
- 直到商品库扩到 60 个做召回评测，建库才开始整批失败
- `bootstrap_product_index` 会吞掉异常降级到关键词召回
- 表现为"向量检索静默失效"而不是报错

解决方案：分批请求，每批不超过 `_MAX_BATCH` 条（默认 10）。

===========================================
为什么按 index 排序？
===========================================
网关返回的 data 数组可能是乱序的（虽然按理说应该按 input 顺序）。
为了安全起见，按 index 字段排序，确保返回的向量与输入的文本一一对应。
"""
from __future__ import annotations

import os

import httpx

from app.domain.catalog.ports.retrieval_ports import EmbeddingClient
from app.infrastructure.settings import Settings

# ============================================
# 单次请求最多带多少条文本
# ============================================
# 可通过环境变量 EMBEDDING_MAX_BATCH 调整
# 默认 10：适配大多数 OpenAI 兼容网关的限制
_MAX_BATCH = int(os.getenv("EMBEDDING_MAX_BATCH", "10"))


class OpenAIEmbeddingClient(EmbeddingClient):
    """
    OpenAI 兼容 Embedding 客户端

    实现了 EmbeddingClient 接口，调用 /v1/embeddings 接口生成向量。

    属性:
        _base_url: API 基础 URL（如 "https://api.openai.com/v1"）
        _api_key: API 密钥
        _model: 模型名称（如 "text-embedding-4"）
        _timeout: 请求超时时间（秒）
    """

    def __init__(self, settings: Settings, timeout_seconds: float = 15.0) -> None:
        """
        初始化 Embedding 客户端

        参数:
            settings: 应用配置
            timeout_seconds: 请求超时时间（默认 15 秒）

        注意:
            base_url 会去掉末尾的 "/"，避免路径拼接出错
        """
        self._base_url = settings.embedding_base_url.rstrip("/")
        self._api_key = settings.embedding_api_key
        self._model = settings.embedding_model
        self._timeout = timeout_seconds

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

        实现:
            内部调用 embed_batch，只取第一个结果
        """
        return (await self.embed_batch([text]))[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        批量文本向量化

        参数:
            texts: 输入文本列表

        返回:
            向量列表（与输入等长，一一对应）

        注意:
            - 分批请求，每批不超过 _MAX_BATCH 条
            - 串行请求（非并发），避免触发网关限流

        示例:
            >>> texts = ["旅行三件套", "露营灯", "登山杖"]
            >>> vectors = await client.embed_batch(texts)
            >>> len(vectors)  # 3

        实现:
            1. 将文本列表分成多个批次
            2. 逐批调用 _embed_chunk
            3. 合并所有结果
        """
        if not texts:
            return []
        vectors: list[list[float]] = []
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            # 分片串行请求：批量上限是网关侧约束，超限不会报错只会返回空 body
            for start in range(0, len(texts), _MAX_BATCH):
                chunk = texts[start : start + _MAX_BATCH]
                vectors.extend(await self._embed_chunk(client, chunk))
        return vectors

    async def _embed_chunk(
        self, client: httpx.AsyncClient, chunk: list[str],
    ) -> list[list[float]]:
        """
        调用 Embedding API（单批次）

        参数:
            client: httpx 异步客户端
            chunk: 本批次的文本列表

        返回:
            向量列表

        异常:
            RuntimeError: 网关返回空 body 或响应格式异常

        实现:
            1. POST 请求 /embeddings 接口
            2. 检查响应是否为空 body（网关超限的特征）
            3. 按 index 排序，确保顺序正确
        """
        response = await client.post(
            f"{self._base_url}/embeddings",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"model": self._model, "input": chunk},
        )
        response.raise_for_status()
        if not response.content:
            # 明确指向批量上限，不要让调用方对着 JSONDecodeError 猜
            raise RuntimeError(
                f"embedding 网关返回空 body（HTTP {response.status_code}，本批 {len(chunk)} 条）："
                f"通常是单次批量超过网关上限，可调小 EMBEDDING_MAX_BATCH（当前 {_MAX_BATCH}）",
            )
        body = response.json()
        if "data" not in body:
            raise RuntimeError(f"embedding 响应异常：{str(body)[:200]}")
        # 按 index 回位，避免网关乱序
        ordered = sorted(body["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in ordered]

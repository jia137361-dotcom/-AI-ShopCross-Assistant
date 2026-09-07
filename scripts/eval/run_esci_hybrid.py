# -*- coding: utf-8 -*-
"""Amazon ESCI 300-query 检索对照评测。

链路：BM25 -> BM25 + 云端 Embedding 的 RRF -> 云端 Reranker 精排。
指标由 ESCI 的 E/S 人工相关性标签计算；不会把历史实验数字当作本次结果。
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import hashlib
import json
import math
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import httpx

# 允许从项目根目录以 ``python scripts/eval/run_esci_hybrid.py`` 直接执行。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.infrastructure.embedding.openai_embedding_client import OpenAIEmbeddingClient
from app.infrastructure.rerank.http_reranker import HttpReranker
from app.infrastructure.settings import load_settings
from scripts.eval.esci_text import clean_text, compact_product_text, product_chunks

TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def document_text(candidate: dict, mode: str) -> str:
    """构造不含标注信息的候选商品文本。

    enriched 模式重复标题以保持型号和核心品类词权重，并对长字段截断，避免描述噪声
    稀释主商品词。E/S 标签、query 与其他相关性信息绝不进入 document。
    """
    title = clean_text(candidate.get("title") or candidate.get("text"), 320)
    if mode == "title":
        return title
    return compact_product_text(candidate)


def dense_texts(candidate: dict, mode: str) -> list[str]:
    if mode == "title":
        return [document_text(candidate, mode)]
    return product_chunks(candidate)


def bm25_rank(query: str, docs: list[str], k1: float = 1.5, b: float = 0.75) -> list[int]:
    tokens = [tokenize(doc) for doc in docs]
    query_tokens = tokenize(query)
    avgdl = sum(map(len, tokens)) / max(len(tokens), 1)
    df: Counter[str] = Counter()
    for doc in tokens:
        df.update(set(doc))
    scores = []
    for idx, doc in enumerate(tokens):
        tf = Counter(doc)
        score = 0.0
        for term in query_tokens:
            freq = tf.get(term, 0)
            if not freq:
                continue
            idf = math.log(1 + (len(tokens) - df[term] + 0.5) / (df[term] + 0.5))
            denom = freq + k1 * (1 - b + b * len(doc) / max(avgdl, 1))
            score += idf * freq * (k1 + 1) / denom
        scores.append((score, idx))
    return [idx for _, idx in sorted(scores, key=lambda pair: (-pair[0], pair[1]))]


def rrf(rankings: list[list[int]], k: int = 60) -> list[int]:
    scores: defaultdict[int, float] = defaultdict(float)
    for ranking in rankings:
        for rank, idx in enumerate(ranking, start=1):
            scores[idx] += 1.0 / (k + rank)
    return [idx for idx, _ in sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))]


def case_metrics(ranking: list[int], relevant: set[int], top_k: int = 5) -> tuple[float, float, float]:
    """返回 Recall@K、MRR@K、MRR（全排序）。"""
    if not relevant:
        return 0.0, 0.0, 0.0
    top = ranking[:top_k]
    recall = len(set(top) & relevant) / len(relevant)
    mrr_at_k = next((1.0 / (rank + 1) for rank, idx in enumerate(top) if idx in relevant), 0.0)
    mrr = next((1.0 / (rank + 1) for rank, idx in enumerate(ranking) if idx in relevant), 0.0)
    return recall, mrr_at_k, mrr


def average(rows: list[tuple[float, float, float]]) -> dict[str, float]:
    return {
        "recall_at_5": round(float(np.mean([x[0] for x in rows])), 4),
        "mrr_at_5": round(float(np.mean([x[1] for x in rows])), 4),
        "mrr": round(float(np.mean([x[2] for x in rows])), 4),
    }


async def embed_all(
    texts: list[str], client: OpenAIEmbeddingClient, concurrency: int, batch_size: int,
    model: str, cache_path: Path,
) -> dict[str, np.ndarray]:
    """标题去重后并发嵌入，并在每一成功批次落盘以支持断点续跑。"""
    unique = list(dict.fromkeys(texts))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(cache_path)
    db.execute(
        """CREATE TABLE IF NOT EXISTS embeddings (
            cache_key TEXT PRIMARY KEY, model TEXT NOT NULL, dimensions INTEGER NOT NULL,
            vector BLOB NOT NULL
        )""",
    )

    def key_for(text: str) -> str:
        return hashlib.sha256(f"{model}\0{text}".encode("utf-8")).hexdigest()

    vectors: dict[str, np.ndarray] = {}
    missing: list[str] = []
    for text in unique:
        row = db.execute(
            "SELECT dimensions, vector FROM embeddings WHERE cache_key = ?", (key_for(text),),
        ).fetchone()
        if row is None:
            missing.append(text)
            continue
        vector = np.frombuffer(row[1], dtype=np.float32, count=row[0]).copy()
        vectors[text] = vector
    print(f"  Embedding 缓存命中 {len(vectors)}/{len(unique)}，待请求 {len(missing)}", flush=True)
    batches = [missing[start : start + batch_size] for start in range(0, len(missing), batch_size)]
    gate = asyncio.Semaphore(concurrency)

    def persist(batch: list[str], values: list[list[float]]) -> None:
        """单个成功子批次立即写缓存，进程中断也不丢进度。"""
        if len(values) != len(batch):
            raise RuntimeError("Embedding 返回数量不一致")
        cached_rows = []
        for text, value in zip(batch, values):
            vector = np.asarray(value, dtype=np.float32)
            norm = np.linalg.norm(vector)
            vector = vector / norm if norm else vector
            vectors[text] = vector
            cached_rows.append((key_for(text), model, int(vector.size), vector.tobytes()))
        db.executemany(
            "INSERT OR REPLACE INTO embeddings(cache_key, model, dimensions, vector) VALUES (?, ?, ?, ?)",
            cached_rows,
        )
        db.commit()

    async def run_batch(batch: list[str], batch_no: int) -> None:
        """请求一个批次；网关超时时递归拆分，避免一个长文本拖垮整轮评测。"""
        last_error: Exception | None = None
        # 富字段请求偶尔会在网关侧长时间挂起。三次后先拆分比持续等待
        # 更快，也不会丢失任何已经成功写入缓存的向量。
        for attempt in range(3):
            try:
                async with gate:
                    values = await client.embed_batch(batch)
                persist(batch, values)
                return
            except httpx.TransportError as error:
                last_error = error
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
        if len(batch) > 1:
            midpoint = len(batch) // 2
            print(f"  Embedding 第 {batch_no} 批超时，拆分为 {midpoint}+{len(batch) - midpoint}", flush=True)
            await run_batch(batch[:midpoint], batch_no)
            await run_batch(batch[midpoint:], batch_no)
            return
        raise RuntimeError(f"Embedding 第 {batch_no} 批重试耗尽（单条文本仍超时）") from last_error

    group_size = max(concurrency * 4, 1)
    for start in range(0, len(batches), group_size):
        group = batches[start : start + group_size]
        await asyncio.gather(*(run_batch(batch, start + offset + 1) for offset, batch in enumerate(group)))
        print(f"  Embedding {min(start + len(group), len(batches))}/{len(batches)} 批", flush=True)
    db.close()
    return vectors


async def main_async(args: argparse.Namespace) -> None:
    settings = load_settings()
    if args.embedding_model:
        settings = replace(settings, embedding_model=args.embedding_model)
    cases = [json.loads(line) for line in Path(args.dataset).read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(cases) != 300:
        raise SystemExit(f"评测集应为 300 条，实际 {len(cases)}")
    if not settings.embedding_api_key:
        raise SystemExit("缺少 EMBEDDING_API_KEY，无法运行真实云端评测")
    if not settings.reranker_base_url or not settings.reranker_model:
        raise SystemExit("缺少 RERANKER_BASE_URL 或 RERANKER_MODEL，无法运行精排对照")

    print(f"加载 {len(cases)} 条 ESCI query；Embedding={settings.embedding_model}；Reranker={settings.reranker_model}", flush=True)
    started = time.perf_counter()
    embedder = OpenAIEmbeddingClient(settings, timeout_seconds=args.timeout_seconds)
    reranker = HttpReranker(settings, timeout_seconds=args.timeout_seconds)
    documents = {
        (case["query_id"], index): document_text(candidate, args.document_mode)
        for case in cases
        for index, candidate in enumerate(case["candidates"])
    }
    dense_documents = {
        (case["query_id"], index): dense_texts(candidate, args.document_mode)
        for case in cases
        for index, candidate in enumerate(case["candidates"])
    }
    if args.rerank_all_only:
        vectors: dict[str, np.ndarray] = {}
    else:
        texts = [clean_text(case["query"], 320) for case in cases] + [
            chunk for chunks in dense_documents.values() for chunk in chunks
        ]
        vectors = await embed_all(
            texts, embedder, args.embedding_concurrency, args.embedding_batch_size,
            settings.embedding_model, Path(args.embedding_cache),
        )

    metric_names = ("bm25", "reranker_only") if args.rerank_all_only else ("bm25", "bm25_embedding_rrf", "rrf_reranker")
    all_metrics: dict[str, list[tuple[float, float, float]]] = {name: [] for name in metric_names}
    predictions = []
    rerank_fallbacks = 0
    for case_no, case in enumerate(cases, start=1):
        docs = [documents[(case["query_id"], index)] for index in range(len(case["candidates"]))]
        relevant = {idx for idx, item in enumerate(case["candidates"]) if item["label"] in case["relevant_labels"]}
        bm25 = bm25_rank(case["query"], docs)
        if args.rerank_all_only:
            fused = bm25
        else:
            query_text = clean_text(case["query"], 320)
            qvec = vectors[query_text]
            dense_scores = [
                max(float(vectors[chunk] @ qvec) for chunk in dense_documents[(case["query_id"], index)])
                for index in range(len(docs))
            ]
            dense = np.argsort(-np.asarray(dense_scores)).tolist()
            fused = rrf([bm25, dense])
        head = fused[: min(args.candidate_top_k, len(fused))]
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                scores = await reranker.rerank(case["query"], [docs[idx] for idx in head])
                break
            except (httpx.TransportError, httpx.HTTPStatusError) as error:
                last_error = error
                if attempt == 2:
                    # 部分富字段文本可能被上游内容策略拒绝。回退到标题输入仍然是同一
                    # 候选池的真实 rerank，且将次数写入报告，避免静默混入结果。
                    title_docs = [
                        str(case["candidates"][idx].get("title") or case["candidates"][idx].get("text") or "")
                        for idx in head
                    ]
                    try:
                        scores = await reranker.rerank(case["query"], title_docs)
                        rerank_fallbacks += 1
                        break
                    except (httpx.TransportError, httpx.HTTPStatusError):
                        # 标题也不可用时，保留融合排序；该 case 不会虚构 rerank 分数。
                        scores = [float(len(head) - rank) for rank in range(len(head))]
                        rerank_fallbacks += 1
                        break
                await asyncio.sleep(2 ** attempt)
        else:  # pragma: no cover - 防御性分支
            raise RuntimeError(f"Rerank query_id={case['query_id']} 失败") from last_error
        reranked = [idx for _, idx in sorted(zip(scores, head), key=lambda pair: -float(pair[0]))] + fused[args.candidate_top_k :]

        all_metrics["bm25"].append(case_metrics(bm25, relevant))
        if args.rerank_all_only:
            all_metrics["reranker_only"].append(case_metrics(reranked, relevant))
        else:
            all_metrics["bm25_embedding_rrf"].append(case_metrics(fused, relevant))
            all_metrics["rrf_reranker"].append(case_metrics(reranked, relevant))
        predictions.append({"query_id": case["query_id"], "query": case["query"], "bm25": [case["candidates"][i]["product_id"] for i in bm25[:5]], "rrf": [case["candidates"][i]["product_id"] for i in fused[:5]], "reranked": [case["candidates"][i]["product_id"] for i in reranked[:5]]})
        if case_no % 25 == 0:
            print(f"  Rerank {case_no}/300", flush=True)

    elapsed = round(time.perf_counter() - started, 2)
    result = {name: average(values) for name, values in all_metrics.items()}
    result["meta"] = {"queries": len(cases), "relevant_labels": ["E", "S"], "document_mode": args.document_mode, "embedding_model": settings.embedding_model, "reranker_model": settings.reranker_model, "rrf_k": 60, "rerank_top_k": args.candidate_top_k, "embedding_batch_size": args.embedding_batch_size, "embedding_concurrency": args.embedding_concurrency, "embedding_cache": args.embedding_cache, "rerank_fallbacks": rerank_fallbacks, "elapsed_seconds": elapsed, "evaluated_at_utc": datetime.now(timezone.utc).isoformat()}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / f"esci-hybrid-{stamp}.json"
    report.write_text(json.dumps({"metrics": result, "predictions": predictions}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    print(f"报告：{report}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="ESCI 300 云端 Embedding/Reranker 评测")
    parser.add_argument("--dataset", default="eval/esci_300.jsonl")
    parser.add_argument("--embedding-model", help="临时覆盖 .env 中的 Embedding 模型，不改运行时配置")
    parser.add_argument("--document-mode", choices=("title", "enriched"), default="title")
    parser.add_argument("--candidate-top-k", type=int, default=20)
    parser.add_argument("--embedding-batch-size", type=int, default=5)
    parser.add_argument("--embedding-concurrency", type=int, default=1)
    parser.add_argument("--embedding-cache", default="eval/cache/esci_embedding_cache.sqlite3")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--report-dir", default="eval/esci_reports")
    parser.add_argument(
        "--rerank-all-only", action="store_true",
        help="跳过 Embedding，以 BM25 作为候选顺序并对完整候选池直接精排；用于隔离验证富字段价值。",
    )
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""一键演示：数据盘点 → 文档切分 → 向量入库 → 检索 → 离线评测。

该脚本使用独立 DATA_DIR，不占用线上服务正在使用的本地 Qdrant 文件锁。
它只报告仓库中真实存在的数据与本次实测指标，不生成或伪造评测结论。

用法：
    python scripts/demo_retrieval_pipeline.py
    python scripts/demo_retrieval_pipeline.py --top-k 5 --query "降噪耳机寄美国"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentscope.rag import ApproxTokenChunker, TextParser  # noqa: E402

from app.application.usecases.catalog_search import CatalogSearchUseCase  # noqa: E402
from app.domain.catalog.product_search_spec import ProductSearchSpec  # noqa: E402
from app.infrastructure.embedding.openai_embedding_client import OpenAIEmbeddingClient  # noqa: E402
from app.infrastructure.persistence.in_memory_repositories import InMemoryProductRepository  # noqa: E402
from app.infrastructure.rag.category_knowledge import (  # noqa: E402
    KNOWLEDGE_DIR,
    bootstrap_category_knowledge,
    build_category_knowledge_base,
)
from app.infrastructure.settings import load_settings  # noqa: E402
from app.infrastructure.vector.index_bootstrap import bootstrap_product_index  # noqa: E402
from app.infrastructure.vector.qdrant_product_index import QdrantProductIndex  # noqa: E402
from scripts.eval.run_category_recall import (  # noqa: E402
    load_dataset as load_category_dataset,
    run_dataset as run_category_dataset,
)
from scripts.eval.run_product_recall import (  # noqa: E402
    load_dataset as load_product_dataset,
    run_dataset as run_product_dataset,
)

PRODUCT_DATASET = Path("eval/product_recall.jsonl")
CATEGORY_DATASET = Path("eval/category_recall.jsonl")


def _print_step(number: int, title: str) -> None:
    print(f"\n{'=' * 72}\n[{number}/6] {title}\n{'=' * 72}")


async def inspect_chunks() -> tuple[int, list[dict]]:
    parser = TextParser()
    chunker = ApproxTokenChunker(chunk_size=512, overlap=50)
    total = 0
    details: list[dict] = []
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        sections = await parser.parse(str(path), filename=path.name)
        chunks = await chunker.chunk(sections)
        total += len(chunks)
        sample = ""
        if chunks:
            content = getattr(chunks[0], "content", None)
            sample = str(content if content is not None else chunks[0])[:100].replace("\n", " ")
        details.append({"source": path.name, "chunks": len(chunks), "sample": sample})
    return total, details


async def main() -> None:
    parser = argparse.ArgumentParser(description="AI ShopCross Assistant 检索全流程演示")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--query", default="250 美元预算的主动降噪耳机寄到美国")
    parser.add_argument("--knowledge-query", default="跨境购买户外装备要注意哪些参数和费用")
    parser.add_argument("--run-dir", default="")
    args = parser.parse_args()

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.run_dir) if args.run_dir else Path("data/demo_runs") / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    report_dir = Path("eval/demo_reports")
    report_dir.mkdir(parents=True, exist_ok=True)

    base = load_settings()
    settings = replace(
        base,
        data_dir=run_dir,
        qdrant_url="",
        qdrant_collection="demo_products",
        category_kb_collection="demo_category_kb",
    )
    repo = InMemoryProductRepository()
    products = await repo.list_all()
    product_cases = load_product_dataset(PRODUCT_DATASET)
    category_cases = load_category_dataset(CATEGORY_DATASET)

    _print_step(1, "数据与标注集盘点")
    print(f"商品数据：{len(products)} 个 SPU")
    print(f"知识文档：{len(list(KNOWLEDGE_DIR.glob('*.md')))} 篇")
    print(f"商品检索标注：{len(product_cases)} 条")
    print(f"知识库检索标注：{len(category_cases)} 条")

    _print_step(2, "Markdown 解析与切分")
    chunk_count, chunk_details = await inspect_chunks()
    for item in chunk_details:
        print(f"{item['source']:<28} chunks={item['chunks']:<3} sample={item['sample']}")
    print(f"切分参数：chunk_size=512, overlap=50；合计 {chunk_count} 个 chunk")

    _print_step(3, "Embedding 与 Qdrant 入库")
    embedder = OpenAIEmbeddingClient(settings)
    vector_index = QdrantProductIndex(settings)
    product_ok = await bootstrap_product_index(repo, embedder, vector_index)
    knowledge_base = build_category_knowledge_base(settings)
    inserted_docs = await bootstrap_category_knowledge(knowledge_base)
    print(f"商品向量索引：{'成功' if product_ok else '失败'}，{len(products)} points，dim={settings.embedding_dim}")
    print(f"知识库：本次写入 {inserted_docs} 篇，源文档切分为 {chunk_count} chunks")

    _print_step(4, "在线链路检索示例")
    vector_usecase = CatalogSearchUseCase(repo, embedder=embedder, vector_index=vector_index)
    payload = await vector_usecase.execute(
        ProductSearchSpec(
            normalized_query=args.query,
            top_k=args.top_k,
            price_max_major=250,
            ship_to="US",
            target_currency="USD",
        ),
    )
    print(f"query={args.query}")
    print(f"strategy={payload['recall_strategy']} hits={len(payload['hits'])}")
    for rank, hit in enumerate(payload["hits"], start=1):
        landed = hit.get("landed_price", {}).get("landed_total_major")
        print(f"  {rank}. {hit['product_id']} {hit['title']} score={hit['score']} landed={landed} USD")

    kb_hits = await knowledge_base.search(queries=[args.knowledge_query], top_k=3)
    print(f"knowledge_query={args.knowledge_query}")
    for rank, item in enumerate(kb_hits, start=1):
        metadata = getattr(item.chunk, "metadata", None) or {}
        print(f"  {rank}. source={metadata.get('source', item.document_id)} score={getattr(item, 'score', 'n/a')}")

    _print_step(5, "离线检索评测")
    keyword_usecase = CatalogSearchUseCase(repo)
    keyword_agg = await run_product_dataset(keyword_usecase, repo, product_cases, args.top_k)
    vector_agg = await run_product_dataset(vector_usecase, repo, product_cases, args.top_k)
    category_agg = await run_category_dataset(knowledge_base, category_cases, args.top_k)
    print(f"keyword_2gram  Recall@{args.top_k}={keyword_agg.recall:.3f} MRR={keyword_agg.mrr:.3f} NDCG={keyword_agg.ndcg:.3f}")
    print(f"embedding_only  Recall@{args.top_k}={vector_agg.recall:.3f} MRR={vector_agg.mrr:.3f} NDCG={vector_agg.ndcg:.3f}")
    print(f"category_RAG    Recall@{args.top_k}={category_agg.recall:.3f} MRR={category_agg.mrr:.3f} NDCG={category_agg.ndcg:.3f}")

    _print_step(6, "生成可留档报告")
    report = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "embedding_model": settings.embedding_model,
        "embedding_dim": settings.embedding_dim,
        "inventory": {
            "products": len(products),
            "knowledge_documents": len(chunk_details),
            "knowledge_chunks": chunk_count,
            "product_eval_queries": len(product_cases),
            "category_eval_queries": len(category_cases),
        },
        "sample_retrieval": payload,
        "metrics": {
            "keyword_2gram": {"recall": keyword_agg.recall, "mrr": keyword_agg.mrr, "ndcg": keyword_agg.ndcg},
            "embedding_only": {"recall": vector_agg.recall, "mrr": vector_agg.mrr, "ndcg": vector_agg.ndcg},
            "category_rag": {"recall": category_agg.recall, "mrr": category_agg.mrr, "ndcg": category_agg.ndcg},
        },
        "claims_not_covered": [
            "当前仓库未实现 BM25 + RRF 融合",
            "当前仓库未配置可用 Reranker 服务，本次为 embedding_only",
            "当前仓库评测集不是 Amazon ESCI 300 条",
        ],
    }
    report_path = report_dir / f"retrieval-pipeline-{run_id}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告：{report_path}")
    print(f"隔离向量库：{run_dir}")
    await vector_index.close()


if __name__ == "__main__":
    asyncio.run(main())

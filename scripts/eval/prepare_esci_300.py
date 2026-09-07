# -*- coding: utf-8 -*-
"""从 Amazon Science 官方 ESCI 数据确定性抽取 300 条 US/test 查询。

输入文件来自 https://github.com/amazon-science/esci-data ：
  shopping_queries_dataset_examples.parquet
  shopping_queries_dataset_products.parquet

为避免下载 1.1GB 商品表，商品 Parquet 可使用官方 GitHub Media URL，DuckDB
通过 HTTP Range 只读取所需列/row group。产物每行是一条 query 及其候选商品集。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import pandas as pd

from scripts.eval.esci_text import clean_text

DEFAULT_DIR = Path("data/external/esci-data/shopping_queries_dataset")
PRODUCTS_REMOTE = (
    "https://media.githubusercontent.com/media/amazon-science/esci-data/main/"
    "shopping_queries_dataset/shopping_queries_dataset_products.parquet"
)


def choose_queries(examples: pd.DataFrame, count: int, seed: int) -> pd.DataFrame:
    subset = examples[
        (examples["small_version"] == 1)
        & (examples["split"] == "test")
        & (examples["product_locale"] == "us")
    ].copy()
    stats = subset.groupby("query_id").agg(
        query=("query", "first"),
        candidates=("product_id", "nunique"),
        relevant=("esci_label", lambda labels: int(labels.isin(["E", "S"]).sum())),
    )
    eligible = stats[(stats["candidates"] >= 10) & (stats["relevant"] >= 1)]
    if len(eligible) < count:
        raise RuntimeError(f"满足条件的 query 只有 {len(eligible)} 条，不足 {count}")
    query_ids = eligible.sample(n=count, random_state=seed).index
    return subset[subset["query_id"].isin(query_ids)].copy()


def load_products(source: str, product_ids: list[str]) -> pd.DataFrame:
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs")
    con.register("wanted", pd.DataFrame({"product_id": product_ids}))
    # 富字段评测需要标题、品牌、卖点和描述。若用远程完整产品表会比较慢；日常
    # 复现实验优先传入已合并字段的 test parquet（--merged-test）。
    return con.execute(
        """
        SELECT p.product_id, p.product_title, p.product_description,
               p.product_bullet_point, p.product_brand, p.product_color,
               p.product_locale
        FROM read_parquet(?) p
        INNER JOIN wanted w USING (product_id)
        WHERE p.product_locale = 'us'
        """,
        [source],
    ).fetchdf()


def clean(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return clean_text(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="准备 Amazon ESCI 300-query 评测集")
    parser.add_argument("--data-dir", default=str(DEFAULT_DIR))
    parser.add_argument("--count", type=int, default=300)
    parser.add_argument("--seed", type=int, default=20260902)
    parser.add_argument("--output", default="eval/esci_300.jsonl")
    parser.add_argument("--products-url", default=PRODUCTS_REMOTE)
    parser.add_argument(
        "--merged-test",
        help="已合并商品字段的 ESCI test parquet；提供后不再读取 1.1GB products 表",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    examples_path = data_dir / "shopping_queries_dataset_examples.parquet"
    products_path = data_dir / "shopping_queries_dataset_products.parquet"
    if not args.merged_test and (not examples_path.exists() or examples_path.stat().st_size < 1_000_000):
        raise SystemExit(f"缺少官方 examples parquet：{examples_path}")

    if args.merged_test:
        merged_path = Path(args.merged_test)
        if not merged_path.exists() or merged_path.stat().st_size < 1_000_000:
            raise SystemExit(f"缺少合并后的 test parquet：{merged_path}")
        examples = pd.read_parquet(
            merged_path,
            columns=[
                "query", "query_id", "product_id", "product_locale", "esci_label",
                "small_version", "split", "product_title", "product_description",
                "product_bullet_point", "product_brand", "product_color",
            ],
        )
    else:
        examples = pd.read_parquet(examples_path)
    selected = choose_queries(examples, args.count, args.seed)
    product_ids = sorted(selected["product_id"].unique().tolist())
    print(f"选中 {selected['query_id'].nunique()} 条 query，{len(selected)} 个判断，{len(product_ids)} 个候选商品")
    if args.merged_test:
        print(f"商品元数据来源：{args.merged_test}")
        merged = selected
    else:
        source = str(products_path) if products_path.exists() and products_path.stat().st_size > 1_000_000 else args.products_url
        print(f"商品元数据来源：{source}")
        products = load_products(source, product_ids)
        merged = selected.merge(products, on=["product_id", "product_locale"], how="left")
    missing = int(merged["product_title"].isna().sum())
    if missing:
        raise RuntimeError(f"有 {missing} 条候选缺失商品标题，拒绝生成不完整评测集")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for query_id, group in merged.groupby("query_id", sort=True):
            candidates = []
            for row in group.itertuples(index=False):
                # 保留原始字段。评测脚本在 title / enriched 两种模式之间切换，
                # 让同一候选池、同一 E/S 标注下的提升可被公平复现。
                candidates.append({
                    "product_id": row.product_id,
                    "label": row.esci_label,
                    "text": clean(row.product_title),  # 兼容旧版标题检索
                    "title": clean(row.product_title),
                    "brand": clean(getattr(row, "product_brand", "")),
                    "color": clean(getattr(row, "product_color", "")),
                    "bullet_point": clean(getattr(row, "product_bullet_point", "")),
                    "description": clean(getattr(row, "product_description", "")),
                })
            record = {
                "query_id": int(query_id), "query": clean(group.iloc[0]["query"]),
                "locale": "us", "split": "test", "relevant_labels": ["E", "S"],
                "candidates": candidates,
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"已生成 {output}（{output.stat().st_size / 1024 / 1024:.2f} MB）")


if __name__ == "__main__":
    main()

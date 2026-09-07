# -*- coding: utf-8 -*-
"""将 Amazon ESCI 评测集的唯一商品导入 ShopCross SQLite 目录表。

ESCI 发布的数据没有价格和库存，因此导入记录明确标记为 ``catalog_only``：
可用于检索与质量分析，不能进入下单链路，也不会伪造价格或可售库存。

默认导入 ``eval/esci_300.jsonl``（300 条 query 的 6k 级候选集）：
    python scripts/import_esci_catalog.py
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.eval.esci_text import clean_text


def _sqlite_path(value: str) -> Path:
    prefix = "sqlite+aiosqlite:///"
    if not value.startswith(prefix):
        raise ValueError("本导入脚本当前仅支持 SQLite DATABASE_URL")
    return Path(value.removeprefix(prefix))


def _products(dataset: Path) -> dict[str, dict[str, str]]:
    products: dict[str, dict[str, str]] = {}
    with dataset.open(encoding="utf-8") as source:
        for line in source:
            case = json.loads(line)
            for candidate in case.get("candidates", []):
                product_id = str(candidate.get("product_id") or "").strip()
                if not product_id:
                    continue
                incoming = {
                    "product_id": product_id,
                    "title": clean_text(candidate.get("title") or candidate.get("text"), 500),
                    "brand": clean_text(candidate.get("brand"), 200),
                    "color": clean_text(candidate.get("color"), 120),
                    "bullet_point": clean_text(candidate.get("bullet_point"), 2500),
                    "description": clean_text(candidate.get("description"), 5000),
                    "locale": str(case.get("locale") or "us"),
                }
                # 同一 ASIN 会在多个 query 出现；保留字段更完整的一条。
                existing = products.get(product_id)
                if existing is None or sum(map(len, incoming.values())) > sum(map(len, existing.values())):
                    products[product_id] = incoming
    return products


def main() -> None:
    parser = argparse.ArgumentParser(description="导入 Amazon ESCI 商品目录到 SQLite")
    parser.add_argument("--dataset", default=str(PROJECT_ROOT / "eval" / "esci_300.jsonl"))
    parser.add_argument("--database", default=str(PROJECT_ROOT / "data" / "shopcross.db"))
    args = parser.parse_args()

    dataset, database = Path(args.dataset), Path(args.database)
    products = _products(dataset)
    if not products:
        raise SystemExit(f"没有从数据集读取到商品：{dataset}")
    database.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(database) as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS amazon_catalog_products (
                product_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                brand TEXT NOT NULL DEFAULT '',
                color TEXT NOT NULL DEFAULT '',
                bullet_point TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                locale TEXT NOT NULL DEFAULT 'us',
                source TEXT NOT NULL DEFAULT 'amazon-esci',
                catalog_only INTEGER NOT NULL DEFAULT 1,
                imported_at TEXT NOT NULL
            )""",
        )
        db.executemany(
            """INSERT INTO amazon_catalog_products
                (product_id, title, brand, color, bullet_point, description, locale, imported_at)
                VALUES (:product_id, :title, :brand, :color, :bullet_point, :description, :locale, :imported_at)
                ON CONFLICT(product_id) DO UPDATE SET
                  title=excluded.title, brand=excluded.brand, color=excluded.color,
                  bullet_point=excluded.bullet_point, description=excluded.description,
                  locale=excluded.locale, imported_at=excluded.imported_at""",
            [{**product, "imported_at": now} for product in products.values()],
        )
        count = db.execute("SELECT count(*) FROM amazon_catalog_products").fetchone()[0]
    print(f"已导入 {len(products)} 个 ESCI 唯一商品；SQLite 目录表当前共 {count} 条：{database}")


if __name__ == "__main__":
    main()

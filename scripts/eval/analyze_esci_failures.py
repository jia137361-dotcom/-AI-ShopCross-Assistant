"""Analyse ESCI relevance structure and complementary retrieval errors."""
from __future__ import annotations

import json
import hashlib
import itertools
from collections import defaultdict
from pathlib import Path


DATASET = Path("eval/esci_300.jsonl")
REPORT = Path("eval/esci_reports/esci-hybrid-20260903-001042.json")


def bucket(value: int) -> str:
    if value <= 2:
        return "1-2"
    if value <= 5:
        return "3-5"
    if value <= 10:
        return "6-10"
    return "11+"


def main() -> None:
    cases = {row["query_id"]: row for row in map(json.loads, DATASET.read_text(encoding="utf-8").splitlines())}
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    groups: dict[str, list[tuple[float, float, float, float]]] = defaultdict(list)
    for prediction in report["predictions"]:
        case = cases[prediction["query_id"]]
        labels = {c["product_id"]: c["label"] for c in case["candidates"]}
        relevant = {pid for pid, label in labels.items() if label in {"E", "S"}}
        scores = []
        for name in ("bm25", "rrf", "reranked"):
            retrieved = prediction[name]
            scores.append(len(set(retrieved) & relevant) / len(relevant))
        union = set(prediction["bm25"]) | set(prediction["rrf"]) | set(prediction["reranked"])
        oracle = min(5, len(union & relevant)) / len(relevant)
        groups[bucket(len(relevant))].append((*scores, oracle))

    total_relevant = [sum(c["label"] in {"E", "S"} for c in case["candidates"]) for case in cases.values()]
    ceiling = sum(min(5, count) / count for count in total_relevant) / len(total_relevant)
    print(f"queries={len(cases)} avg_relevant={sum(total_relevant)/len(total_relevant):.2f} recall@5_ceiling={ceiling:.4f}")
    print("bucket|queries|bm25|rrf|reranker|oracle_union")
    for name in ("1-2", "3-5", "6-10", "11+"):
        rows = groups[name]
        values = [sum(row[i] for row in rows) / len(rows) for i in range(4)]
        print(f"{name}|{len(rows)}|" + "|".join(f"{value:.4f}" for value in values))

    # 用固定 2/3 dev、1/3 holdout 检查三路排名融合是否有可泛化增益。
    samples = []
    for prediction in report["predictions"]:
        case = cases[prediction["query_id"]]
        relevant = {c["product_id"] for c in case["candidates"] if c["label"] in {"E", "S"}}
        holdout = int(hashlib.sha256(str(prediction["query_id"]).encode()).hexdigest()[:8], 16) % 3 == 0
        samples.append((prediction, relevant, holdout))

    def fused_recall(sample, weights):
        prediction, relevant, _ = sample
        scores = defaultdict(float)
        for key, weight in zip(("bm25", "rrf", "reranked"), weights):
            for rank, pid in enumerate(prediction[key], 1):
                scores[pid] += weight / (10 + rank)
        top = sorted(scores, key=lambda pid: (-scores[pid], pid))[:5]
        return len(set(top) & relevant) / len(relevant)

    dev = [sample for sample in samples if not sample[2]]
    test = [sample for sample in samples if sample[2]]
    candidates = [weights for weights in itertools.product(range(5), repeat=3) if any(weights)]
    best = max(candidates, key=lambda weights: sum(fused_recall(s, weights) for s in dev))
    for label, split in (("dev", dev), ("holdout", test), ("all", samples)):
        value = sum(fused_recall(s, best) for s in split) / len(split)
        print(f"fusion_{label}|n={len(split)}|weights={best}|recall@5={value:.4f}")


if __name__ == "__main__":
    main()

"""触发一条真实购物请求，用于验证 Langfuse Trace 采集。"""
from __future__ import annotations

import json
import argparse
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", default="langfuse-e2e-shopping-001")
    parser.add_argument("--buyer-id", default="observability-demo")
    args = parser.parse_args()
    payload = json.dumps(
        {
            "shopping_session_id": args.session_id,
            "buyer_id": args.buyer_id,
            "locale": "zh-CN",
            "currency": "USD",
            "raw_query": "推荐一款适合通勤的降噪耳机，预算250美元",
        },
    ).encode()
    request = urllib.request.Request(
        "http://127.0.0.1:8000/commerce/intents",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        print(response.status)
        print(response.read().decode())


if __name__ == "__main__":
    main()

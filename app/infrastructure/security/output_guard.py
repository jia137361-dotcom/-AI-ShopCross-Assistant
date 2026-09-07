# -*- coding: utf-8 -*-
"""
output_guard L4 输出审核（Output Audit）

===========================================
什么是输出审核？
===========================================
Agent 的最终回复推给买家之前，检查是否夹带了内部实现信息。

为什么需要？
- Agent 可能在回复中意外泄露：
  - 内部会话 ID（shopping_session_id）
  - API 密钥（sk-...）
  - 内部服务地址（vllm、qdrant 等容器主机名）
  - 内部工具名（product_search_tool 等）

===========================================
设计决策
===========================================

1. **脱敏不阻断回复**
   - 命中即脱敏，把 (是否安全) 回给调用方去发告警事件
   - 脱敏动作本身不阻断回复
   - 否则一次误判就等于整轮对话失败

2. **刻意收窄的范围**
   - 文档示例里有 `item_id`，本项目 schema 中不存在，故不纳入
   - `product_id`（如 P1001）**不脱敏**——它本就随商品卡下发给前端，属于对外契约
   - 内部工具名按真实工具集逐个列出，不用宽泛模式，避免误伤

3. **判据只有一条**
   - 脱敏对象必须是买家侧无需知道、且泄露有害的东西
"""
from __future__ import annotations

import re

# 脱敏占位符
REDACTED = "[已脱敏]"

# ============================================
# 真实存在的内部工具名（与 app/application/tools/ 一致）
# ============================================
_INTERNAL_TOOLS = (
    "product_search_tool",
    "category_insight_tool",
    "web_search_tool",
    "create_order_tool",
    "query_order_tool",
    "cancel_order_tool",
    "remember_preference_tool",
    "task_dispatch",
)

# ============================================
# 敏感模式列表
# ============================================
SENSITIVE_PATTERNS: list[str] = [
    # 会话内部 ID（如 shopping_session_id=session-abc123）
    r"shopping_session_id\s*[:=]\s*[\w-]+",
    # API Key 形态（如 sk-xxxxxxxxxxxx）
    r"sk-[a-zA-Z0-9]{20,}",
    # 内部服务地址（容器网络内主机名，如 http://vllm:8000/v1）
    r"https?://(?:vllm|reranker|qdrant|redis|opensearch)(?::\d+)?(?:/\S*)?",
    # 内部工具名（精确匹配，避免误伤）
    r"\b(?:" + "|".join(_INTERNAL_TOOLS) + r")\b",
]

# 预编译正则（提升性能）
_compiled = [re.compile(pattern) for pattern in SENSITIVE_PATTERNS]


def audit_output(text: str) -> tuple[bool, str]:
    """
    审核最终输出

    参数:
        text: Agent 的最终回复文本

    返回:
        (是否安全, 处理后的文本)
        - 安全 = 未命中任何敏感模式
        - 命中时文本已就地脱敏，可直接下发

    示例:
        >>> audit_output("正常回复")
        (True, "正常回复")
        >>> audit_output("请调用 product_search_tool")
        (False, "请调用 [已脱敏]")

    注意:
        - 脱敏不阻断回复，只替换敏感内容
        - 调用方根据第一个返回值决定是否发告警事件
    """
    if not text:
        return True, text

    safe = True
    cleaned = text
    for pattern in _compiled:
        cleaned, count = pattern.subn(REDACTED, cleaned)
        if count:
            safe = False
    return safe, cleaned

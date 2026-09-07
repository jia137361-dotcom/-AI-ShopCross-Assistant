# -*- coding: utf-8 -*-
"""
assertions 断言系统（三类单步断言）

===========================================
什么是断言（Assertion）？
===========================================
断言是检查"某件事是否成立"的规则。
如果不成立，记录失败，但不中断整个流程。

本模块实现三类断言：

    Schema（结构断言）: 工具返回是否是结构完整的合法 JSON        <1ms   纯校验
    Sequencing（顺序断言）: 工具调用顺序是否满足前置条件            <1ms   规则判断
    Semantic（语义断言）: 工具返回与买家诉求是否语义对齐          ~50ms  轻量 LLM（默认不启用）

===========================================
核心约定：断言失败一律不 raise
===========================================
断言失败**不抛出异常**，只记进 `assertions_failed`。

为什么？
- 护栏的目的是让模型**自愈**，不是把一次格式抖动升级成整轮失败
- Agent 在下一轮 Think 时会看到失败提示，自己纠正

===========================================
两处刻意的实现差异
===========================================

1. **调用记录按会话隔离**
   - 文档示例是模块级 `_called_tools: list`
   - 并发多会话会互相污染（A 会话的调用记录算到 B 头上）
   - 这里改为按 shopping_session_id 分桶

2. **写路径前置校验采用"有证据才硬拒"**
   - 文档说 create_order 必须硬拒绝
   - 但会话可能从 AgentState 快照恢复（进程重启后内存记录为空）
   - 此时硬拒会把合法下单误杀
   - 规则收紧为：
     - 本会话已观测到工具调用、却没有一次检索 → 硬拒
     - 完全没有观测记录（刚恢复/首次调用）→ 降级为警告
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

# ============================================
# 工具返回结构定义：工具名 → 必需字段
# ============================================
# 逐个对过真实工具实现：
# - product_search 回 hits/recall_strategy
# - category_insight 回 insights
# - 订单三工具回 Order.snapshot()
TOOL_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "product_search_tool": ("hits", "recall_strategy"),  # 商品检索：必须有 hits 和 recall_strategy
    "category_insight_tool": ("insights",),              # 品类洞察：必须有 insights
    "create_order_tool": ("order_id", "status"),         # 创建订单：必须有 order_id 和 status
    "query_order_tool": ("order_id", "status"),          # 查询订单：同上
    "cancel_order_tool": ("order_id", "status"),         # 取消订单：同上
}

# ============================================
# 工具调用顺序定义：工具名 → 前置工具
# ============================================
PREREQUISITES: dict[str, tuple[str, ...]] = {
    "create_order_tool": ("product_search_tool",),  # 下单前必须先检索商品
    "cancel_order_tool": ("query_order_tool",),     # 取消前必须先查询订单
}

# ============================================
# 写路径：前置不满足时硬拒（前提是有观测证据）
# ============================================
HARD_REJECT_TOOLS = frozenset({"create_order_tool"})  # 创建订单是写路径，风险最高


@dataclass
class AssertionOutcome:
    """
    断言结论（值对象）

    属性:
        failures: 失败列表（每个失败是一个字典，包含 type/tool/reason）
        warnings: 警告列表
        reject_reason: 拒绝原因（不为 None 表示硬拒）
    """

    failures: list[dict[str, str]] = field(default_factory=list)  # 失败列表
    warnings: list[str] = field(default_factory=list)             # 警告列表
    reject_reason: Optional[str] = None                           # 拒绝原因

    @property
    def rejected(self) -> bool:
        """是否被硬拒"""
        return self.reject_reason is not None


def check_schema(tool_name: str, tool_result: Any) -> AssertionOutcome:
    """
    Schema 断言：检查工具返回是否是含必需字段的合法 JSON

    参数:
        tool_name: 工具名
        tool_result: 工具返回值（可能是字符串、字典、其他类型）

    返回:
        AssertionOutcome 断言结论

    逻辑:
        1. 不在检查范围 → 直接通过
        2. 是 "[error] ..." 文本 → 通过（错误返回不算 schema 违约）
        3. 不是合法 JSON → 失败
        4. 不是 JSON 对象 → 失败
        5. 缺少必需字段 → 失败

    示例:
        >>> check_schema("product_search_tool", '{"hits": [], "recall_strategy": "test"}')
        AssertionOutcome(failures=[])  # 通过
        >>> check_schema("product_search_tool", '{"hits": []}')
        AssertionOutcome(failures=[{"reason": "缺少必需字段：recall_strategy"}])
    """
    outcome = AssertionOutcome()
    required = TOOL_REQUIRED_FIELDS.get(tool_name)
    if not required:
        return outcome  # 不在检查范围

    data: Any = tool_result
    if isinstance(tool_result, str):
        # 工具错误返回是 "[error] ..." 文本，不是 JSON——不算 schema 违约
        if tool_result.lstrip().startswith("[error]"):
            return outcome
        try:
            data = json.loads(tool_result)
        except (json.JSONDecodeError, TypeError):
            outcome.failures.append(
                {"type": "schema", "tool": tool_name, "reason": "工具返回不是合法 JSON"},
            )
            return outcome

    if not isinstance(data, dict):
        outcome.failures.append(
            {"type": "schema", "tool": tool_name, "reason": "工具返回不是 JSON 对象"},
        )
        return outcome

    # 检查必需字段
    missing = [key for key in required if key not in data]
    if missing:
        outcome.failures.append(
            {
                "type": "schema",
                "tool": tool_name,
                "reason": f"缺少必需字段：{', '.join(missing)}",
            },
        )
    return outcome


@dataclass
class SequencingTracker:
    """
    顺序追踪器

    按会话记录已调用的工具，做顺序断言。

    属性:
        _called: 会话 ID → 已调用工具列表

    注意:
        - 按会话隔离，避免并发多会话互相污染
        - 进程重启后内存记录会丢失（此时降级为警告）
    """

    _called: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    def record(self, session_id: str, tool_name: str) -> None:
        """
        记录一次工具调用

        参数:
            session_id: 会话 ID
            tool_name: 工具名
        """
        self._called[session_id].append(tool_name)

    def called(self, session_id: str) -> list[str]:
        """
        获取会话已调用的工具列表

        参数:
            session_id: 会话 ID

        返回:
            已调用工具列表
        """
        return list(self._called.get(session_id, []))

    def reset(self, session_id: str) -> None:
        """
        重置会话记录（一轮结束后调用）

        参数:
            session_id: 会话 ID
        """
        self._called.pop(session_id, None)

    def check(self, session_id: str, tool_name: str) -> AssertionOutcome:
        """
        Sequencing 断言：检查前置工具是否已调用过

        参数:
            session_id: 会话 ID
            tool_name: 当前要调用的工具

        返回:
            AssertionOutcome 断言结论

        逻辑:
            1. 没有前置要求 → 通过
            2. 前置工具已调用 → 通过
            3. 前置工具未调用：
               - 是写路径且有观测证据 → 硬拒
               - 其他情况 → 警告

        示例:
            >>> tracker.record("session-1", "product_search_tool")
            >>> tracker.check("session-1", "create_order_tool")
            AssertionOutcome()  # 通过（已检索）
            >>> tracker.check("session-2", "create_order_tool")
            AssertionOutcome(warnings=[...])  # 警告（无观测证据）
        """
        outcome = AssertionOutcome()
        prerequisites = PREREQUISITES.get(tool_name)
        if not prerequisites:
            return outcome

        history = self._called.get(session_id, [])
        for prereq in prerequisites:
            if prereq in history:
                continue  # 前置工具已调用

            if tool_name in HARD_REJECT_TOOLS and history:
                # 有观测证据（本会话调过工具）却没检索过 → 硬拒，钱不能错扣
                outcome.reject_reason = (
                    f"{tool_name} 需要先执行 {prereq}：本会话尚未检索过商品，"
                    f"请先确认买家要买的具体商品再下单。"
                )
            else:
                outcome.warnings.append(
                    f"注意：{tool_name} 通常在 {prereq} 之后调用，但当前 {prereq} 尚未执行。",
                )
            break
        return outcome

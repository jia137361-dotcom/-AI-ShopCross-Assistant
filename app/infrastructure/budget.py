# -*- coding: utf-8 -*-
"""
budget Token 预算管理（Token Budget）

===========================================
为什么 Agent 必须做请求级预算？
===========================================
普通 LLM 应用：一次请求 = 一次调用，成本可控。
Agent：一次意图 = 多轮 Think/Act + 多次往返，单条请求就能打穿成本。

举例：
- 买家说"帮我规划一次旅行"
- Agent 可能调用 10 次工具，每次工具返回大量数据
- 每轮都要把完整上下文发给模型
- 一次请求可能消耗 100K+ token

===========================================
两种预算的区别
===========================================
- ReplyBudgetControlMiddleware：管**单轮回复**的长度（AgentScope 内置）
- 本模块（TokenBudget）：管**整条意图**的累计消耗

两者互补。

===========================================
四档降级（按剩余预算比例）
===========================================

    > 50%      main      主模型，无限制
    20% - 50%  lite      切轻量模型（更便宜）
    5% - 20%   minimal   轻量模型 + 注入简洁模式 hint，压 Think 长度
    < 5%       fallback  不再调 LLM，用已有中间结果做规则兜底

===========================================
为什么用 ContextVar？
===========================================
ContextVar 是 Python 的"协程局部变量"：
- 每个协程有独立的值
- 子协程继承父协程的值

好处：
- 一次意图一个预算实例
- 子 Agent 派发时协程继承 ContextVar，天然共享同一份预算账本
- 不同买家的请求互不干扰

===========================================
默认不启用
===========================================
`TOKEN_BUDGET_TOTAL=0` 表示不启用。
它会改变模型选择，必须是显式开启的选择。
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ============================================
# 档位阈值（剩余比例下界）
# ============================================
TIER_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (0.50, "main"),      # 剩余 > 50%：主模型
    (0.20, "lite"),      # 剩余 20%-50%：轻量模型
    (0.05, "minimal"),   # 剩余 5%-20%：轻量模型 + 简洁模式
    (0.0, "fallback"),   # 剩余 < 5%：不再调 LLM
)

# 简洁模式提示（minimal 档注入）
MINIMAL_MODE_HINT = (
    "当前对话 Token 预算已接近上限，请进入简洁模式："
    "不要展开推理过程，直接给出结论与最关键的 2-3 条依据。"
)

# 预算耗尽通知（fallback 档返回）
FALLBACK_NOTICE = (
    "本轮 Token 预算已耗尽，未再调用模型。以下是基于已获取信息的整理结果。"
)


@dataclass
class TokenBudget:
    """
    Token 账本（一条意图的预算）

    属性:
        total_limit: 总预算（token 数）
        used: 已消耗（token 数）
        entries: 消耗记录（来源, token 数）
    """

    total_limit: int                                    # 总预算
    used: int = 0                                       # 已消耗
    entries: list[tuple[str, int]] = field(default_factory=list)  # 消耗记录

    def charge(self, source: str, tokens: int) -> None:
        """
        记录一次消耗

        参数:
            source: 消耗来源（如 "llm"、"tool"）
            tokens: 消耗的 token 数
        """
        if tokens <= 0:
            return
        self.used += tokens
        self.entries.append((source, tokens))

    @property
    def remaining(self) -> int:
        """剩余预算"""
        return max(0, self.total_limit - self.used)

    @property
    def remaining_ratio(self) -> float:
        """剩余比例（0.0 - 1.0）"""
        if self.total_limit <= 0:
            return 1.0
        return self.remaining / self.total_limit

    @property
    def tier(self) -> str:
        """
        当前档位

        返回:
            "main" / "lite" / "minimal" / "fallback"
        """
        ratio = self.remaining_ratio
        for lower_bound, tier in TIER_THRESHOLDS:
            if ratio > lower_bound:
                return tier
        return "fallback"

    @property
    def exhausted(self) -> bool:
        """是否已耗尽（fallback 档）"""
        return self.tier == "fallback"


# ContextVar：协程局部的预算变量
_budget_var: ContextVar[Optional[TokenBudget]] = ContextVar("token_budget", default=None)


def init_budget(total_limit: int) -> Optional[TokenBudget]:
    """
    在意图入口初始化预算

    参数:
        total_limit: 总预算（token 数），<= 0 表示不启用

    返回:
        TokenBudget 实例，不启用时返回 None

    示例:
        >>> budget = init_budget(100000)  # 10 万 token 预算
        >>> init_budget(0)  # 不启用
        None
    """
    if total_limit <= 0:
        _budget_var.set(None)
        return None
    budget = TokenBudget(total_limit=total_limit)
    _budget_var.set(budget)
    return budget


def get_budget() -> Optional[TokenBudget]:
    """
    获取当前预算

    返回:
        当前预算实例，未启用时返回 None
    """
    return _budget_var.get()


def current_tier() -> str:
    """
    获取当前档位

    返回:
        "main" / "lite" / "minimal" / "fallback"
        未启用预算时恒为 "main"

    注意:
        调用方无需分支判断是否启用
    """
    budget = get_budget()
    return budget.tier if budget is not None else "main"


def resolve_model(main_model: str, lite_model: str) -> str:
    """
    按当前档位选择模型

    参数:
        main_model: 主模型名
        lite_model: 轻量模型名

    返回:
        应使用的模型名

    注意:
        fallback 档不该走到这里（调用方应先判 exhausted 直接规则兜底）
        真走到了也返回 lite，避免因为选不出模型把整轮打挂
    """
    tier = current_tier()
    if tier == "main":
        return main_model
    return lite_model or main_model


def minimal_mode_hint() -> Optional[str]:
    """
    获取简洁模式提示

    返回:
        minimal 档时返回提示文本，否则返回 None

    注意:
        用于注入到消息中，让模型进入简洁模式
    """
    return MINIMAL_MODE_HINT if current_tier() == "minimal" else None

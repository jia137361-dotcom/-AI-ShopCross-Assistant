# -*- coding: utf-8 -*-
"""
drift_detector 静默漂移检测器（Silent Drift Detector）

===========================================
什么是静默漂移？
===========================================
**每一步都没错，但 5 步之后已经偏离了目标。**

举例：
- 买家要"旅行三件套"
- Agent 第 1 步：搜索"旅行三件套" → 正确
- Agent 第 2 步：看到有"旅行枕"，搜索"旅行枕" → 还行
- Agent 第 3 步：看到"充气颈枕"，搜索"充气颈枕" → 有点偏
- Agent 第 4 步：看到"充气用品"，搜索"充气用品" → 更偏
- Agent 第 5 步：推荐"充气皮划艇" → 完全跑偏！

单步断言看得见"这一步格式对不对"，看不见"这一串动作还在不在为原始诉求服务"。

===========================================
四类信号（前三类纯计算、零成本）
===========================================

1. **目标遗忘**（Goal Forgetting）
   - 最近若干轮行为里原始 query 关键词的命中率 < 20%
   - 比如原始 query 是"旅行三件套"，最近都在搜"充气用品"

2. **探索发散**（Exploration Divergence）
   - 连续 3 次检索返回空候选
   - 说明 Agent 可能在无效搜索

3. **偏好丢失**（Preference Loss）
   - 候选属性命中长期偏好黑名单
   - 比如买家说"不要塑料"，候选里出现了塑料材质

4. **成本失控**（Cost Spike）
   - 最近几轮平均 token > 历史均值 × 2
   - 说明 Agent 可能在低效循环

===========================================
两处刻意的实现差异
===========================================

1. **按会话隔离**
   - 文档示例用模块级 `_round_counter` 全局计数
   - 并发多会话会互相干扰
   - 这里所有状态按 shopping_session_id 分桶

2. **LLM 终审是可选注入**
   - 默认只跑纯计算信号（零成本、可单测）
   - 需要语义终审时由装配层注入一个 async judge
   - 避免本模块硬依赖模型层

===========================================
默认不启用
===========================================
即便纯计算部分零成本，注入纠正提示也会改变模型行为。
应当是显式开启的选择（`DRIFT_DETECT_ENABLED=1`）。
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# ============================================
# 检测参数
# ============================================
CHECK_INTERVAL = 3              # 每 N 轮检测一次（控制成本）
KEYWORD_HIT_FLOOR = 0.2         # 目标遗忘：关键词命中率下界（20%）
EMPTY_RESULT_LIMIT = 3          # 探索发散：连续空结果次数
COST_SPIKE_MULTIPLIER = 2.0     # 成本失控：相对历史均值的倍数

# 纠正提示模板
CORRECTION_HINT = (
    "检测到你的动作可能已偏离买家的原始诉求（{reasons}）。"
    "请回到买家最初的需求：{original_query}。"
    "先复述你理解的需求要点，再据此选择下一步动作。"
)

# 关键词提取正则：英文/数字整词 + 中文 2 字以上
_WORD_RE = re.compile(r"[a-zA-Z0-9]+|[一-鿿]{2,}")


def extract_keywords(text: str) -> set[str]:
    """
    极简关键词抽取

    规则:
        - 英文/数字：整词提取（转小写）
        - 中文：2 字以上提取（用于匹配）

    参数:
        text: 输入文本

    返回:
        关键词集合

    示例:
        >>> extract_keywords("旅行三件套 抗造")
        {'旅行', '三件', '件套', '抗造'}

    注意:
        与 catalog_search.tokenize 同思路，但这里只做漂移判定，不参与召回
    """
    keywords: set[str] = set()
    for token in _WORD_RE.findall(text or ""):
        if token.isascii():
            keywords.add(token.lower())
            continue
        # 中文：提取 2-gram
        keywords.update(token[i : i + 2] for i in range(len(token) - 1))
    return keywords


@dataclass
class _SessionTrace:
    """
    会话行为轨迹（内部值对象）

    记录一个会话的行为历史，用于漂移检测。

    属性:
        original_query: 原始查询（买家最初的意图）
        keywords: 原始查询的关键词集合
        rounds: 已执行的轮数
        recent_actions: 最近 3 轮的动作摘要
        consecutive_empty: 连续空结果次数
        token_history: 每轮消耗的 token 数
    """

    original_query: str = ""                           # 原始查询
    keywords: set[str] = field(default_factory=set)    # 关键词
    rounds: int = 0                                    # 轮数
    recent_actions: list[str] = field(default_factory=list)  # 最近动作
    consecutive_empty: int = 0                         # 连续空结果
    token_history: list[int] = field(default_factory=list)  # token 历史


@dataclass
class DriftReport:
    """
    漂移检测报告（值对象）

    属性:
        reasons: 命中的原因列表
        verdict: 最终判定（"正常" 或其他）
    """

    reasons: list[str] = field(default_factory=list)  # 命中的原因
    verdict: str = "正常"                              # 最终判定

    @property
    def drifted(self) -> bool:
        """是否漂移"""
        return bool(self.reasons) or self.verdict != "正常"

    def hint(self, original_query: str) -> str:
        """
        生成纠正提示

        参数:
            original_query: 原始查询

        返回:
            纠正提示文本
        """
        return CORRECTION_HINT.format(
            reasons="；".join(self.reasons) or self.verdict,
            original_query=original_query,
        )


# LLM 终审函数类型（可选注入）
LlmJudge = Callable[[str, str], Awaitable[str]]


@dataclass
class DriftDetector:
    """
    静默漂移检测器

    按会话累积行为轨迹，周期性判定是否漂移。

    属性:
        check_interval: 检测间隔（轮数）
        judge: LLM 终审函数（可选）
        _traces: 会话 ID → 行为轨迹
    """

    check_interval: int = CHECK_INTERVAL          # 检测间隔
    judge: Optional[LlmJudge] = None              # LLM 终审（可选）
    _traces: dict[str, _SessionTrace] = field(default_factory=lambda: defaultdict(_SessionTrace))

    def start_turn(self, session_id: str, original_query: str) -> None:
        """
        开始新一轮（记录原始查询）

        参数:
            session_id: 会话 ID
            original_query: 买家原始查询

        注意:
            只在第一次调用时记录（后续调用不覆盖）
        """
        trace = self._traces[session_id]
        if not trace.original_query:
            trace.original_query = original_query
            trace.keywords = extract_keywords(original_query)

    def observe_action(
        self,
        session_id: str,
        summary: str,
        *,
        result_empty: bool = False,
        tokens: int = 0,
    ) -> None:
        """
        记录一次 Act 的摘要与结果特征

        参数:
            session_id: 会话 ID
            summary: 动作摘要（如 "product_search_tool 检索露营灯"）
            result_empty: 结果是否为空
            tokens: 本轮消耗的 token 数
        """
        trace = self._traces[session_id]
        trace.rounds += 1
        trace.recent_actions.append(summary)
        del trace.recent_actions[:-3]  # 只留最近 3 轮
        trace.consecutive_empty = trace.consecutive_empty + 1 if result_empty else 0
        if tokens > 0:
            trace.token_history.append(tokens)

    def due(self, session_id: str) -> bool:
        """
        是否到了检测轮次

        参数:
            session_id: 会话 ID

        返回:
            True 表示该检测了

        逻辑:
            每 check_interval 轮检测一次
        """
        trace = self._traces[session_id]
        return trace.rounds > 0 and trace.rounds % self.check_interval == 0

    def computational_signals(
        self,
        session_id: str,
        blacklist_hits: Optional[list[str]] = None,
    ) -> list[str]:
        """
        计算四类信号（纯计算、零成本）

        参数:
            session_id: 会话 ID
            blacklist_hits: 命中黑名单的属性列表

        返回:
            命中的原因列表
        """
        trace = self._traces[session_id]
        reasons: list[str] = []

        # 1. 目标遗忘：最近动作与原始需求关键词命中率 < 20%
        if trace.keywords and trace.recent_actions:
            recent = extract_keywords(" ".join(trace.recent_actions))
            hit_ratio = len(trace.keywords & recent) / len(trace.keywords)
            if hit_ratio < KEYWORD_HIT_FLOOR:
                reasons.append(f"最近动作与原始需求关键词命中率仅 {hit_ratio:.0%}")

        # 2. 探索发散：连续 3 次检索无候选
        if trace.consecutive_empty >= EMPTY_RESULT_LIMIT:
            reasons.append(f"连续 {trace.consecutive_empty} 次检索无候选")

        # 3. 偏好丢失：候选命中买家黑名单属性
        if blacklist_hits:
            reasons.append(f"候选命中买家黑名单属性：{', '.join(blacklist_hits)}")

        # 4. 成本失控：最近 3 轮平均 token > 历史均值 × 2
        history = trace.token_history
        if len(history) >= 6:
            recent_avg = sum(history[-3:]) / 3
            baseline = sum(history[:-3]) / len(history[:-3])
            if baseline > 0 and recent_avg > baseline * COST_SPIKE_MULTIPLIER:
                reasons.append(
                    f"最近 3 轮平均 token（{recent_avg:.0f}）超历史均值（{baseline:.0f}）两倍",
                )
        return reasons

    async def check(
        self,
        session_id: str,
        blacklist_hits: Optional[list[str]] = None,
    ) -> DriftReport:
        """
        周期性漂移判定

        参数:
            session_id: 会话 ID
            blacklist_hits: 命中黑名单的属性列表

        返回:
            DriftReport 检测报告

        逻辑:
            1. 未到轮次 → 返回"正常"
            2. 计算纯计算信号
            3. 有信号命中 → 返回报告
            4. 无信号且有 LLM judge → 调用 LLM 终审
        """
        if not self.due(session_id):
            return DriftReport()

        report = DriftReport(reasons=self.computational_signals(session_id, blacklist_hits))

        # 纯计算已判定漂移就不必再花钱问模型
        if report.reasons or self.judge is None:
            return report

        trace = self._traces[session_id]
        if not trace.original_query or not trace.recent_actions:
            return report
        try:
            verdict = await self.judge(trace.original_query, "\n".join(trace.recent_actions))
            report.verdict = (verdict or "正常").strip()
        except Exception as err:
            logger.warning("漂移终审失败，按正常处理：%s", err)
        return report

    def original_query(self, session_id: str) -> str:
        """获取原始查询"""
        return self._traces[session_id].original_query

    def reset(self, session_id: str) -> None:
        """一轮结束后清理"""
        self._traces.pop(session_id, None)

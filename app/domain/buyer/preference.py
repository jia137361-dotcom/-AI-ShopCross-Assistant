# -*- coding: utf-8 -*-
"""
BuyerPreference 买家偏好（Value Object）+ PreferenceStore 端口（Port）

===========================================
什么是买家偏好？
===========================================
买家在对话中表达的长期喜好或忌口，跨会话保存：
- like（正向偏好）："喜欢小众设计"
- dislike（负向偏好/黑名单）："不要塑料材质"

===========================================
为什么 dislike 不能按相关性截断？
===========================================
假设买家偏好：
1. "喜欢小众设计"（like）
2. "不要塑料材质"（dislike）
3. "喜欢军绿色"（like）
...

当买家问"推荐个咖啡杯"时：
- query 与"喜欢小众设计"的向量相似度 → 高（都和设计有关）
- query 与"不要塑料材质"的向量相似度 → 低（咖啡杯和塑料无关）

如果按相关性 top_k 截断，"不要塑料材质"会被丢掉 → 推出塑料杯！

这是**安全问题**（推了用户明确拒绝的东西），不是相关性问题。

所以规则：**dislike 全量保留，like 才按相关性截断**。

===========================================
为什么删除要精确匹配？
===========================================
"不要塑料"和"不要塑料包装"向量相似度很高。
如果支持模糊匹配删除，买家说"删除不要塑料"可能会误删"不要塑料包装"。

删除是不可逆操作，必须精确匹配！
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone

# 有效的偏好类型
VALID_KINDS = ("like", "dislike")


@dataclass(frozen=True)
class BuyerPreference:
    """
    买家偏好值对象

    属性:
        buyer_id: 买家 ID
        kind: 偏好类型（"like" 或 "dislike"）
        statement: 偏好陈述（如"不要塑料材质"）
        created_at: 创建时间（ISO 格式，自动填充）

    不变量:
        - kind 必须是 "like" 或 "dislike"
        - statement 不能为空
    """

    buyer_id: str          # 买家 ID
    kind: str              # like / dislike
    statement: str         # 偏好陈述
    created_at: str = ""   # 创建时间（自动填充）

    def __post_init__(self) -> None:
        """
        创建后的验证（不变量检查）

        确保:
        1. kind 必须是 "like" 或 "dislike"
        2. statement 不能为空
        3. created_at 自动填充当前时间

        注意:
            frozen=True 下修改字段需要用 object.__setattr__
        """
        if self.kind not in VALID_KINDS:
            raise ValueError(f"BuyerPreference.kind 必须是 {VALID_KINDS}：{self.kind}")
        if not self.statement or not self.statement.strip():
            raise ValueError("BuyerPreference.statement required")
        if not self.created_at:
            # frozen=True 下不能直接赋值，需要用 object.__setattr__
            object.__setattr__(self, "created_at", datetime.now(timezone.utc).isoformat())


class PreferenceStore(ABC):
    """
    偏好存储端口（接口）

    这是 domain 层定义的抽象接口，infrastructure 提供具体实现。

    两套实现:
        - JsonFilePreferenceStore: JSON 文件存储（本地开发）
        - SqlPreferenceStore: SQLite 存储（生产）

    为什么用端口-适配器模式？
        - 切换存储方式不需要改业务代码
        - 测试时可以用内存实现
    """

    @abstractmethod
    async def append(self, preference: BuyerPreference) -> None:
        """
        追加偏好

        参数:
            preference: 要保存的偏好

        注意:
            同一买家同一 statement 幂等去重（不会重复保存）
        """
        ...

    @abstractmethod
    async def list_by_buyer(self, buyer_id: str) -> list[BuyerPreference]:
        """
        获取买家的所有偏好

        参数:
            buyer_id: 买家 ID

        返回:
            偏好列表（按创建时间排序）
        """
        ...

    @abstractmethod
    async def delete(self, buyer_id: str, statement: str) -> bool:
        """
        删除偏好（精确匹配 statement）

        参数:
            buyer_id: 买家 ID
            statement: 偏好原文（必须完全一致）

        返回:
            True 表示删除成功，False 表示未找到

        设计决策:
            刻意不做模糊/向量匹配：删偏好是不可逆写操作，
            而"不要塑料"与"不要塑料包装"相似度极高，模糊匹配会误删。

            未命中时调用方应把现存偏好列表回给模型，让它用原文重试。

        注意:
            不按 kind 区分：同 statement 的 like 与 dislike 条目会一并清除。
        """
        ...

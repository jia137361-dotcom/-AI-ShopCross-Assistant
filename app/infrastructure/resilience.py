# -*- coding: utf-8 -*-
"""
ToolResilienceMiddleware 工具韧性中间件

===========================================
什么是韧性（Resilience）？
===========================================
系统在部分组件故障时，仍能继续提供服务的能力。

本中间件提供两种保护：
1. **超时保护**（Timeout）：工具执行超过阈值即中断，防止长时间挂起
2. **熔断保护**（Circuit Breaker）：连续失败后暂时禁用工具，防止级联故障

===========================================
什么是熔断器（Circuit Breaker）？
===========================================
熔断器有三种状态：

    ┌─────────┐
    │  CLOSED │ ← 正常状态，所有请求都放行
    └────┬────┘
         │ 连续失败达阈值（默认 3 次）
         ▼
    ┌─────────┐
    │   OPEN  │ ← 熔断状态，所有请求直接返回错误（短路）
    └────┬────┘
         │ 冷却期满（默认 60 秒）
         ▼
    ┌──────────┐
    │ HALF_OPEN│ ← 半开状态，放一次探测请求
    └────┬─────┘
         │ 成功 → 回到 CLOSED
         │ 失败 → 回到 OPEN

类比：家用电路保险丝，电流过大时熔断，保护电器。

===========================================
为什么按工具名维度熔断？
===========================================
不同工具的下游依赖不同：
- product_search_tool 依赖 Qdrant
- create_order_tool 依赖 SQLite
- web_search_tool 依赖 Tavily

Qdrant 挂了只影响检索，不应阻止下单。
如果按 Agent 维度熔断，会导致"检索挂了连下单都不让做"。

===========================================
什么是洋葱式拦截（Onion Interception）？
===========================================
AgentScope 2.0 的中间件是洋葱式的：

    HarnessToolMiddleware（外层）
        ↓ pre_tool_call
        ToolResilienceMiddleware（内层）
            ↓ 超时/熔断保护
            实际工具执行
        ↑ post_tool_call
    ↑

先做准入判定（顺序/循环），再进超时/熔断保护。
被硬拒的调用不会白白占用一次熔断名额。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Optional

from agentscope.message import TextBlock, ToolResultState
from agentscope.tool import ToolBase, ToolChunk, ToolMiddlewareBase

from app.infrastructure.context import ShoppingContext
from app.infrastructure.eventbus import TradeEventBus

logger = logging.getLogger(__name__)

# ============================================
# 工具超时分级（秒）
# ============================================
# 检索/知识库偏长（涉及向量计算），订单要快（简单 CRUD），子代理调度最宽松（包含完整子 Agent 执行）
DEFAULT_TIMEOUTS: dict[str, float] = {
    "product_search_tool": 15.0,      # 商品检索（embedding + Qdrant + rerank）
    "category_insight_tool": 15.0,    # 品类知识库检索
    "web_search_tool": 20.0,          # 联网搜索（网络延迟大）
    "create_order_tool": 10.0,        # 创建订单（简单 CRUD）
    "query_order_tool": 10.0,         # 查询订单
    "cancel_order_tool": 10.0,        # 取消订单
    "remember_preference_tool": 10.0, # 记住偏好
    "task_dispatch": 180.0,           # 子代理调度（包含完整子 Agent 执行）
}
_FALLBACK_TIMEOUT = 30.0  # 默认超时（未配置的工具）


@dataclass
class _CircuitState:
    """
    熔断器内部状态（值对象）

    属性:
        consecutive_failures: 连续失败次数
        opened_at: 熔断打开时间（None 表示未打开）
        half_open_probing: 是否正在半开探测
    """

    consecutive_failures: int = 0    # 连续失败次数
    opened_at: Optional[float] = None # 熔断打开时间
    half_open_probing: bool = False   # 是否正在半开探测

    @property
    def status(self) -> str:
        """
        获取当前状态

        返回:
            "closed" / "open" / "half_open"
        """
        if self.opened_at is None:
            return "closed"  # 未打开
        return "half_open" if self.half_open_probing else "open"


@dataclass
class CircuitBreakerRegistry:
    """
    熔断器注册表（进程内共享）

    按工具名维护熔断状态，同一工具在不同 Agent 实例间共享故障视图。

    属性:
        failure_threshold: 连续失败达此次数后熔断（默认 3）
        reset_seconds: 熔断后多久转半开（默认 60 秒）
        _states: 工具名 → 熔断状态
    """

    failure_threshold: int = 3             # 熔断阈值
    reset_seconds: float = 60.0            # 冷却时间
    _states: dict[str, _CircuitState] = field(default_factory=dict)  # 状态表

    def _state(self, tool_name: str) -> _CircuitState:
        """获取工具的熔断状态（不存在则创建）"""
        return self._states.setdefault(tool_name, _CircuitState())

    def status(self, tool_name: str) -> str:
        """
        获取工具的熔断状态

        参数:
            tool_name: 工具名

        返回:
            "closed" / "open" / "half_open"
        """
        return self._state(tool_name).status

    def allow(self, tool_name: str, now: Optional[float] = None) -> bool:
        """
        是否放行本次调用

        参数:
            tool_name: 工具名
            now: 当前时间（用于测试，不传则用 time.monotonic()）

        返回:
            True 表示放行，False 表示熔断中

        逻辑:
            - CLOSED → 放行
            - OPEN → 冷却期内不放行，冷却期满转半开并放行一次
            - HALF_OPEN → 放行（探测请求）
        """
        state = self._state(tool_name)
        if state.opened_at is None:
            return True  # CLOSED，放行
        elapsed = (now or time.monotonic()) - state.opened_at
        if elapsed < self.reset_seconds:
            return False  # OPEN，冷却期内，不放行
        state.half_open_probing = True
        return True  # HALF_OPEN，放行一次探测

    def record_success(self, tool_name: str) -> None:
        """
        记录成功（重置熔断状态）

        参数:
            tool_name: 工具名

        注意:
            任何一次成功都会重置连续失败计数
        """
        self._states[tool_name] = _CircuitState()

    def record_failure(self, tool_name: str, now: Optional[float] = None) -> None:
        """
        记录失败

        参数:
            tool_name: 工具名
            now: 当前时间

        逻辑:
            - 半开探测失败 → 重新打开熔断器
            - 普通失败 → 连续失败计数 +1，达阈值则熔断
        """
        state = self._state(tool_name)
        if state.half_open_probing:
            # 半开探测再次失败：重新打开并重置冷却计时
            state.opened_at = now or time.monotonic()
            state.half_open_probing = False
            return
        state.consecutive_failures += 1
        if state.consecutive_failures >= self.failure_threshold:
            state.opened_at = now or time.monotonic()


class ToolResilienceMiddleware(ToolMiddlewareBase):
    """
    工具超时 + 熔断中间件

    功能:
        1. 超时保护：工具执行超过阈值即中断
        2. 熔断保护：连续失败后暂时禁用工具

    熔断注册表可以是：
        - CircuitBreakerRegistry: 进程内共享
        - SharedCircuitBreakerRegistry: Redis 支撑，跨实例共享
    """

    def __init__(
        self,
        registry: CircuitBreakerRegistry,
        bus: Optional[TradeEventBus] = None,
        timeouts: Optional[dict[str, float]] = None,
    ) -> None:
        """
        初始化中间件

        参数:
            registry: 熔断器注册表
            bus: 事件总线（用于发布熔断事件）
            timeouts: 工具超时配置（不传则用 DEFAULT_TIMEOUTS）
        """
        self._registry = registry
        self._bus = bus
        self._timeouts = timeouts or DEFAULT_TIMEOUTS

    def _timeout_for(self, tool_name: str) -> float:
        """
        获取工具的超时时间

        参数:
            tool_name: 工具名

        返回:
            超时时间（秒）
        """
        return self._timeouts.get(tool_name, _FALLBACK_TIMEOUT)

    def _publish_circuit(self, tool_name: str, circuit: str, detail: str) -> None:
        """
        发布熔断事件

        参数:
            tool_name: 工具名
            circuit: 熔断状态
            detail: 详细信息
        """
        if self._bus is None:
            return
        self._bus.publish(
            ShoppingContext.current_session_id(),
            "tool.result",
            {"tool": tool_name, "circuit": circuit, "error": detail},
        )

    async def on_tool_call(
        self,
        tool: ToolBase,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator[ToolChunk, None]],
    ) -> AsyncGenerator[ToolChunk, None]:
        """
        工具调用拦截（核心方法）

        参数:
            tool: 被调用的工具
            input_kwargs: 工具入参
            next_handler: 下一个处理器（实际工具执行）

        执行流程:
            1. 检查熔断状态 → 熔断中则直接返回错误
            2. 在超时保护内执行工具
            3. 成功 → 记录成功，重置熔断
            4. 失败 → 记录失败，可能触发熔断

        Yields:
            ToolChunk: 工具执行结果
        """
        tool_name = tool.name

        # ============================================
        # 1. 检查熔断状态
        # ============================================
        if not await _allow(self._registry, tool_name):
            detail = f"{tool_name} 连续失败已熔断，暂不可用，请稍后再试或改用其他方式"
            logger.warning("工具熔断短路：%s", tool_name)
            self._publish_circuit(tool_name, "open", detail)
            yield ToolChunk(
                content=[TextBlock(type="text", text=f"[error] {detail}")],
                state=ToolResultState.ERROR,
            )
            return

        # ============================================
        # 2. 在超时保护内执行工具
        # ============================================
        timeout = self._timeout_for(tool_name)
        chunks: list[ToolChunk] = []
        try:
            # 先在超时保护内收集全部 chunk，再对外 yield
            # 保证超时能被拦在中间件内部，不会把半截流交给 Agent
            async def _collect() -> list[ToolChunk]:
                collected: list[ToolChunk] = []
                async for chunk in next_handler(**input_kwargs):
                    collected.append(chunk)
                return collected

            chunks = await asyncio.wait_for(_collect(), timeout=timeout)
        except asyncio.TimeoutError:
            # 超时 → 记录失败
            await _record_failure(self._registry, tool_name)
            detail = f"{tool_name} 执行超过 {timeout:.0f} 秒已中断"
            logger.warning("工具超时：%s（%.0fs）", tool_name, timeout)
            self._publish_circuit(tool_name, await _status(self._registry, tool_name), detail)
            yield ToolChunk(
                content=[TextBlock(type="text", text=f"[error] {detail}")],
                state=ToolResultState.ERROR,
            )
            return
        except Exception as err:
            # 异常 → 记录失败
            await _record_failure(self._registry, tool_name)
            detail = f"{tool_name} 执行异常：{err}"
            logger.warning("工具异常：%s（%s）", tool_name, err)
            self._publish_circuit(tool_name, await _status(self._registry, tool_name), detail)
            yield ToolChunk(
                content=[TextBlock(type="text", text=f"[error] {detail}")],
                state=ToolResultState.ERROR,
            )
            return

        # ============================================
        # 3. 记录成功/失败
        # ============================================
        # 工具自身返回 ERROR 也计入连续失败（如下游 5xx 持续报错）
        if chunks and chunks[-1].state == ToolResultState.ERROR:
            await _record_failure(self._registry, tool_name)
        else:
            await _record_success(self._registry, tool_name)

        for chunk in chunks:
            yield chunk


# ============================================
# 注册表适配函数
# ============================================
# 共享实现（Redis）的读写是异步的，本地实现是同步的
# 这里统一走适配函数，优先调用异步方法


async def _allow(registry: Any, tool_name: str) -> bool:
    """检查是否放行（适配同步/异步）"""
    if hasattr(registry, "allow_async"):
        return await registry.allow_async(tool_name)
    return registry.allow(tool_name)


async def _record_failure(registry: Any, tool_name: str) -> None:
    """记录失败（适配同步/异步）"""
    if hasattr(registry, "record_failure_async"):
        await registry.record_failure_async(tool_name)
        return
    registry.record_failure(tool_name)


async def _record_success(registry: Any, tool_name: str) -> None:
    """记录成功（适配同步/异步）"""
    if hasattr(registry, "record_success_async"):
        await registry.record_success_async(tool_name)
        return
    registry.record_success(tool_name)


async def _status(registry: Any, tool_name: str) -> str:
    """获取状态（适配同步/异步）"""
    if hasattr(registry, "status_async"):
        return await registry.status_async(tool_name)
    return registry.status(tool_name)

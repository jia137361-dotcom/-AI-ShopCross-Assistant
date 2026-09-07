# -*- coding: utf-8 -*-
"""
GatewayThrottle 网关限流器（Gateway Throttle）

===========================================
什么是限流？
============================================
限制对下游服务（大模型网关）的请求频率，防止触发配额限制。

实测网关会报两种错误：
1. "Too many concurrent requests"（同时在飞的请求超限）
2. "Request rate increased too quickly"（速率爬升过快）

===========================================
两种限制
===========================================
1. **并发限制**（Concurrency Limit）：同时在飞的请求数不超过 N
   - 使用 asyncio.Semaphore 实现

2. **速率限制**（Rate Limit）：相邻请求的间隔不小于 M 秒
   - 使用 asyncio.Lock + sleep 实现

为什么两个都要治？
- 只限并发：挡不住一批请求同时起跑（比如 10 个请求同时发起）
- 只限速率：挡不住长请求堆积（一个请求持续 60 秒，期间其他请求被阻塞）

===========================================
注意：流式请求的特殊处理
===========================================
流式请求返回的是异步生成器，数据需要一段时间才能读完。
如果 `async with slot()` 在数据还没读完时就退出，名额会被释放，限流等于没做。

解决方案：ThrottledChatModel 把名额持有到流真正耗尽。
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator


class GatewayThrottle:
    """
    网关限流器

    同时约束并发数和请求速率。

    属性:
        _semaphore: 信号量（控制并发数）
        _interval_lock: 锁（串行化起跑时刻）
        _min_interval: 最小间隔（秒）
        _last_start: 上次请求的起跑时间
    """

    def __init__(self, max_concurrency: int, min_interval_seconds: float) -> None:
        """
        初始化限流器

        参数:
            max_concurrency: 最大并发数（如 2）
            min_interval_seconds: 最小请求间隔（秒，如 1.0）
        """
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))  # 信号量
        self._interval_lock = asyncio.Lock()                          # 间隔锁
        self._min_interval = max(0.0, min_interval_seconds)           # 最小间隔
        self._last_start = 0.0                                        # 上次起跑时间

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """
        占用一个请求名额（上下文管理器）

        使用方式:
            async with throttle.slot():
                # 在这里执行请求
                response = await client.chat(...)

        注意:
            流式请求必须把本上下文持有到流耗尽！
            否则名额会在数据还没读完时就被释放，限流等于没做。

        示例:
            >>> async with throttle.slot():
            ...     async for chunk in stream:
            ...         process(chunk)  # 流读完后才释放名额
        """
        async with self._semaphore:
            await self._space_out()
            yield

    async def _space_out(self) -> None:
        """
        间隔控制（私有方法）

        逻辑:
            1. 计算距离上次请求的时间间隔
            2. 如果间隔不足 min_interval，sleep 等待
            3. 更新上次起跑时间

        注意:
            使用 Lock 串行化，保证多个请求不会同时通过检查
        """
        if self._min_interval <= 0:
            return
        # 串行化起跑时刻，保证相邻两次请求的间隔不小于 min_interval
        async with self._interval_lock:
            wait = self._min_interval - (time.monotonic() - self._last_start)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_start = time.monotonic()

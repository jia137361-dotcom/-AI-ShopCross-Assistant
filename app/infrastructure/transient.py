# -*- coding: utf-8 -*-
"""
transient 瞬时故障判断（Transient Error Detection）

===========================================
什么是瞬时故障？
===========================================
Transient（瞬时）故障是暂时性的，重试后可能恢复：
- 网络超时
- 网关限流（429）
- 服务端临时不可用（503）

与之相对的是 Permanent（永久）故障，重试也没用：
- 请求格式错误（400）
- 认证失败（401）
- 资源不存在（404）

===========================================
为什么按 message 特征匹配而不是按异常类型？
===========================================
OpenAI 兼容网关把限流错误写在 SSE 流中间时：
- 抛出的是笼统的 `openai.APIError`
- 类型上无法与真实业务错误区分
- 只能看错误文案

同一套判据还要覆盖：
- httpx 超时
- 网关 5xx 错误

===========================================
模型层与编排层共用同一份判据
===========================================
避免两处标记表各自漂移（不一致）。
"""
from __future__ import annotations

# ============================================
# 瞬时故障标记列表（全小写匹配）
# ============================================
# "throttling" 来自实测：网关限流返回 code=Throttling.Concurrency
_TRANSIENT_ERROR_MARKERS = (
    "too many concurrent",       # 并发过多
    "rate limit",                # 速率限制
    "request rate",              # 请求速率
    "too many requests",         # 请求过多
    "throttling",                # 限流
    "429",                       # HTTP 429 Too Many Requests
    "timeout",                   # 超时
    "timed out",                 # 超时
    "temporarily unavailable",   # 临时不可用
    "service unavailable",       # HTTP 503
    "internal server error",     # HTTP 500
    "bad gateway",               # HTTP 502
    "connection reset",          # 连接重置
    "connection error",          # 连接错误
)


def is_transient_error(error: BaseException) -> bool:
    """
    判断异常是否属于可重试的上游瞬时故障

    参数:
        error: 异常对象

    返回:
        True 表示是瞬时故障（可重试），False 表示永久故障（不应重试）

    示例:
        >>> is_transient_error(Exception("rate limit exceeded"))
        True
        >>> is_transient_error(ValueError("invalid input"))
        False

    注意:
        匹配时转为小写，所以大小写不敏感
    """
    message = str(error).lower()
    return any(marker in message for marker in _TRANSIENT_ERROR_MARKERS)

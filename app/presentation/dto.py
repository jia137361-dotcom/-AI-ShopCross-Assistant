# -*- coding: utf-8 -*-
"""presentation DTO

REST 请求 / 响应模型。shopping_session_id 缺省时由服务端生成。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class SubmitIntentRequest(BaseModel):
    shopping_session_id: Optional[str] = Field(default=None, description="会话 ID，缺省则新建会话")
    # 兼容旧客户端字段；服务端会忽略它，改从签名身份 token 取得买家 ID。
    buyer_id: Optional[str] = Field(default=None, description="已废弃：由服务端身份 token 决定")
    locale: str = Field(default="zh-CN")
    currency: str = Field(default="CNY")
    raw_query: str = Field(min_length=1, description="买家自然语言购物意图")


class SubmitIntentResponse(BaseModel):
    shopping_session_id: str
    final_text: str


class CancelOrderRequest(BaseModel):
    reason: str = Field(min_length=1, description="取消原因")

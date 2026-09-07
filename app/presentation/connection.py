# -*- coding: utf-8 -*-
"""ConnectionManager

WebSocket 连接管理：客户端连上 /commerce/events 后发送
{"shopping_session_id": "...", "token": "..."} 完成订阅，服务端把 TradeEventBus
中对应会话的事件推送给该连接。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict

from fastapi import WebSocket, WebSocketDisconnect

from app.presentation.account_auth import verify_token
from app.infrastructure.eventbus import TradeEventBus

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self, bus: TradeEventBus, conversation_store: object, auth_secret: str) -> None:
        self._bus = bus
        self._conversation_store = conversation_store
        self._auth_secret = auth_secret

    async def serve(self, websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            subscribe_payload = await websocket.receive_json()
        except WebSocketDisconnect:
            return
        session_id = subscribe_payload.get("shopping_session_id")
        token = subscribe_payload.get("token")
        if not isinstance(session_id, str) or not session_id:
            await websocket.close(code=4000, reason="缺少 shopping_session_id")
            return
        try:
            principal = verify_token(token, self._auth_secret)
        except Exception:
            await websocket.close(code=4401, reason="登录状态无效")
            return
        session = await self._conversation_store.find_session(session_id)  # type: ignore[attr-defined]
        if session is not None and session["buyer_id"] != principal.buyer_id:
            await websocket.close(code=4403, reason="无权订阅此会话")
            return

        queue = self._bus.subscribe(session_id)
        logger.info("WebSocket 已订阅会话：%s", session_id)
        try:
            # 浏览器必须收到这一确认才显示“服务在线”。TCP/WS 握手成功并不代表
            # 身份和会话归属校验已经通过。
            await websocket.send_json({"type": "connection.ready", "payload": {}})
            while True:
                event = await queue.get()
                payload = asdict(event)
                payload.pop("shopping_session_id", None)
                await websocket.send_json(payload)
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        finally:
            self._bus.unsubscribe(session_id, queue)
            logger.info("WebSocket 已退订会话：%s", session_id)

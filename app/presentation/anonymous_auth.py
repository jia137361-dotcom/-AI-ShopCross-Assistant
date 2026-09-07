"""免注册匿名身份的签发与校验。

浏览器只保存服务端签名的 token；真正用于数据隔离的 buyer_id 从 token
中恢复，绝不采用请求体或查询参数中的 buyer_id。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass

from fastapi import HTTPException


@dataclass(frozen=True)
class AnonymousPrincipal:
    buyer_id: str


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_anonymous_identity(secret: str) -> tuple[AnonymousPrincipal, str]:
    if not secret:
        raise RuntimeError("AUTH_SECRET 未配置，无法签发匿名身份")
    buyer_id = f"anon-{secrets.token_urlsafe(24)}"
    signature = hmac.new(secret.encode(), buyer_id.encode(), hashlib.sha256).digest()
    return AnonymousPrincipal(buyer_id), f"{_encode(buyer_id.encode())}.{_encode(signature)}"


def verify_anonymous_token(token: str | None, secret: str) -> AnonymousPrincipal:
    if not token or not secret:
        raise HTTPException(status_code=401, detail="缺少或无效的匿名身份")
    try:
        encoded_id, encoded_signature = token.split(".", 1)
        buyer_id = _decode(encoded_id).decode("utf-8")
        supplied = _decode(encoded_signature)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=401, detail="缺少或无效的匿名身份") from None
    expected = hmac.new(secret.encode(), buyer_id.encode(), hashlib.sha256).digest()
    if not buyer_id.startswith("anon-") or len(buyer_id) > 128 or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="缺少或无效的匿名身份")
    return AnonymousPrincipal(buyer_id)

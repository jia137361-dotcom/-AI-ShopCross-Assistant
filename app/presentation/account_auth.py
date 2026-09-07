"""账号密码认证与无状态访问 token。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.infrastructure.persistence.sql.tables import AccountRow

_PBKDF2_ITERATIONS = 600_000


@dataclass(frozen=True)
class AccountPrincipal:
    buyer_id: str
    username: str


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _validate_username(username: str) -> str:
    # 不限制字符类型、大小写或长度规则；仅拒绝空账号，避免无法区分身份。
    if not username:
        raise HTTPException(status_code=422, detail="账号不能为空")
    return username


def _validate_password(password: str) -> None:
    if not password:
        raise HTTPException(status_code=422, detail="密码不能为空")


def _password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${_encode(salt)}${_encode(digest)}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt, digest = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        expected = _password_hash(password, _decode(salt)).split("$", 3)[3]
        return hmac.compare_digest(expected, digest)
    except (ValueError, TypeError):
        return False


def issue_token(principal: AccountPrincipal, secret: str) -> str:
    if not secret:
        raise RuntimeError("AUTH_SECRET 未配置，无法签发访问 token")
    payload = f"{principal.buyer_id}:{principal.username}".encode()
    signature = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    return f"{_encode(payload)}.{_encode(signature)}"


def verify_token(token: str | None, secret: str) -> AccountPrincipal:
    if not token or not secret:
        raise HTTPException(status_code=401, detail="请先登录")
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        payload = _decode(encoded_payload)
        supplied = _decode(encoded_signature)
        buyer_id, username = payload.decode("utf-8").split(":", 1)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=401, detail="登录状态无效，请重新登录") from None
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    if not buyer_id.startswith("user-") or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="登录状态无效，请重新登录")
    return AccountPrincipal(buyer_id=buyer_id, username=username)


async def register(engine: AsyncEngine | None, username: str, password: str) -> AccountPrincipal:
    if engine is None:
        raise HTTPException(status_code=503, detail="当前部署未启用账号存储")
    username = _validate_username(username)
    _validate_password(password)
    principal = AccountPrincipal(buyer_id=f"user-{uuid.uuid4().hex}", username=username)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            db.add(AccountRow(buyer_id=principal.buyer_id, username=username, password_hash=_password_hash(password)))
            await db.commit()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="该账号已被注册") from None
    return principal


async def login(engine: AsyncEngine | None, username: str, password: str) -> AccountPrincipal:
    if engine is None:
        raise HTTPException(status_code=503, detail="当前部署未启用账号存储")
    username = _validate_username(username)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        row = await db.scalar(select(AccountRow).where(AccountRow.username == username))
    if row is None or not _verify_password(password, row.password_hash):
        raise HTTPException(status_code=401, detail="账号或密码错误")
    return AccountPrincipal(buyer_id=row.buyer_id, username=row.username)

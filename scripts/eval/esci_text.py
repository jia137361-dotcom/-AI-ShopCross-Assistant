"""ESCI 商品文本清洗与字段感知分块。"""
from __future__ import annotations

import html
import re
import unicodedata

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_SPLIT_RE = re.compile(r"(?:\s*[|•·]\s*|(?<=[.!?;。！？；])\s+)")
_BOILERPLATE = re.compile(
    r"(?i)\b(?:click here|buy now|customer satisfaction|money back guarantee|"
    r"copyright|all rights reserved|actual color may vary)\b"
)


def clean_text(value: object, limit: int = 0) -> str:
    """去 HTML、实体、控制字符、模板噪声，并做 Unicode/空白标准化。"""
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = _TAG_RE.sub(" ", text)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\ufffd", " ")
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    text = _SPACE_RE.sub(" ", text).strip(" -|,;")
    if not text or _BOILERPLATE.fullmatch(text):
        return ""
    return text[:limit].rstrip() if limit and len(text) > limit else text


def unique_segments(value: object, *, segment_limit: int = 420, total_limit: int = 1200) -> list[str]:
    """切分卖点/描述并去重；保序，避免重复营销句占满上下文。"""
    text = clean_text(value)
    seen: set[str] = set()
    result: list[str] = []
    used = 0
    for raw in _SPLIT_RE.split(text):
        segment = clean_text(raw, segment_limit)
        fingerprint = re.sub(r"\W+", "", segment).lower()
        if len(fingerprint) < 3 or fingerprint in seen or _BOILERPLATE.search(segment):
            continue
        if used + len(segment) > total_limit:
            break
        seen.add(fingerprint)
        result.append(segment)
        used += len(segment)
    return result


def compact_product_text(candidate: dict) -> str:
    """供 BM25/Reranker 使用：保留身份与关键属性，舍弃大段原始描述。"""
    title = clean_text(candidate.get("title") or candidate.get("text"), 320)
    brand = clean_text(candidate.get("brand"), 120)
    color = clean_text(candidate.get("color"), 100)
    bullets = unique_segments(candidate.get("bullet_point"), total_limit=700)[:4]
    description = unique_segments(candidate.get("description"), total_limit=500)[:2]
    lines = [f"title: {title}"]
    if brand:
        lines.append(f"brand: {brand}")
    if color:
        lines.append(f"color: {color}")
    if bullets:
        lines.append("features: " + "; ".join(bullets))
    if description:
        lines.append("details: " + "; ".join(description))
    return "\n".join(lines)


def product_chunks(candidate: dict) -> list[str]:
    """供向量召回使用的多向量块：每块带标题身份，属性不会失去商品语境。"""
    title = clean_text(candidate.get("title") or candidate.get("text"), 320)
    brand = clean_text(candidate.get("brand"), 120)
    color = clean_text(candidate.get("color"), 100)
    identity = " | ".join(part for part in (title, brand, color) if part)
    prefix = f"product: {title}"
    chunks = [identity]
    features = unique_segments(candidate.get("bullet_point"), segment_limit=280, total_limit=700)[:4]
    if features:
        chunks.append(f"{prefix}\nfeatures: {'; '.join(features)}")
    details = unique_segments(candidate.get("description"), segment_limit=320, total_limit=500)[:2]
    if details:
        chunks.append(f"{prefix}\ndetails: {'; '.join(details)}")
    return list(dict.fromkeys(chunk for chunk in chunks if chunk))

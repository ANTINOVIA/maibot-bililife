from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlsplit

import ipaddress
import re
import socket
import unicodedata

import aiohttp


SECRET_KEYS = re.compile(r"cookie|token|password|passwd|secret|authorization|sessdata|bili_jct|credential|api_key|qrcode_key", re.I)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): "[已隐藏]" if SECRET_KEYS.search(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)(SESSDATA|bili_jct|authorization|api[_-]?key|password|token|cookie)\s*[=:]\s*[^\s,;\"']+", r"\1=[已隐藏]", value)
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "[已隐藏]", value)
    return value


def normalized(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", text).lower() if c.isalnum())


def similar(left: str, right: str) -> bool:
    a, b = normalized(left), normalized(right)
    return a == b or SequenceMatcher(None, a, b).ratio() >= 0.86


def safe_public_text(text: str) -> None:
    if not 2 <= len(text.strip()) <= 350 or not normalized(text):
        raise ValueError("发言正文长度须为 2..350 字")
    if any(unicodedata.category(c) == "Cc" for c in text):
        raise ValueError("正文含控制字符")
    if re.search(r"SESSDATA|bili_jct|api[_-]?key|system prompt|系统提示词|内部指令|私聊记录|BEGIN .*PRIVATE KEY|\bsk-[\w-]{8,}", text, re.I):
        raise ValueError("正文可能含私密资料或内部指令")
    if redact(text) != text:
        raise ValueError("正文可能含秘密")


def guard_unobserved_senses(text: str, evidence: dict) -> None:
    """ASR 转录也不等于向模型提供了音乐声学分析；禁止凭字幕编造听感。"""
    for sentence in re.split(r"[。；;!?！?\n]", text):
        if "audio_analysis" not in evidence.get("types", []) and re.search(r"(?:字幕|转录).{0,12}(?:错|误识别|不准确)", sentence):
            if not re.search(r"可能|疑似|不确定|未核对|未经核对|待核|是否|不能判断|无法判断", sentence):
                raise ValueError("未核对原音频，不能断言字幕或转录错误")
        if "audio_analysis" not in evidence.get("types", []) and re.search(r"听起来|听到了|音色|旋律|调子|音准|唱得|歌声", sentence):
            if not re.search(r"没|未|无|不能|无法|不确定|不评价|不判断|尚待", sentence):
                raise ValueError("没有音频分析证据，不能生成听感或音乐质量判断")
        if not {"keyframes", "direct_video"}.intersection(evidence.get("types", [])) and re.search(r"画面中|镜头里|看见了|看到了", sentence):
            if not re.search(r"没|未|无|不能|无法|不确定", sentence):
                raise ValueError("没有画面证据，不能生成观看画面经历")


def media_url(raw: str) -> str:
    value = "https:" + raw if raw.startswith("//") else raw
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not any(host == s or host.endswith("." + s) for s in ("bilibili.com", "hdslb.com", "bilivideo.com", "biliapi.net", "bilivideo.cn"))):
        raise ValueError("媒体地址须为允许的 B 站 HTTPS 域名")
    return value


class PublicResolver(aiohttp.abc.AbstractResolver):
    """在连接使用的 DNS 解析点拒绝所有私网地址，避免二次解析竞态。"""

    async def resolve(self, host, port=0, family=socket.AF_INET):
        import asyncio
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, family=family, type=socket.SOCK_STREAM)
        rows = []
        for fam, _, proto, _, address in infos:
            if not ipaddress.ip_address(address[0]).is_global:
                raise ValueError("拒绝媒体域名解析到内部或保留地址")
            rows.append({"hostname": host, "host": address[0], "port": port, "family": fam, "proto": proto, "flags": socket.AI_NUMERICHOST})
        return rows

    async def close(self):
        pass

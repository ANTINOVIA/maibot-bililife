from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

import asyncio
import html
import json
import re
import time

import aiohttp
from bilibili_api import comment, search, user, video

from .security import PublicResolver, media_url
from .vendor.client import BiliClient, LoginRequired, PlatformError, select_subtitles
from .vendor.models import VideoItem


def parse_video(value):
    if re.fullmatch(r"BV[0-9A-Za-z]{10}", value):
        return value, 1
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.hostname not in ("www.bilibili.com", "bilibili.com") or parsed.username or parsed.password:
        raise ValueError("请输入 BV 号或 B 站 HTTPS 视频链接")
    match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10})/?", parsed.path)
    if not match:
        raise ValueError("无效视频链接")
    from urllib.parse import parse_qs
    page = int(parse_qs(parsed.query).get("p", ["1"])[0])
    if not 1 <= page <= 100:
        raise ValueError("分 P 范围为 1..100")
    return match.group(1), page


class BilibiliAdapter(BiliClient):
    def __init__(self, data, store, config, day):
        super().__init__(data / "credential.json")
        self.store, self.config, self.day = store, config, day
        self.check = lambda: None
        async def request_guard(request):
            self.check()
        self.http.event_hooks["request"] = [request_guard]
        self.public_http.event_hooks["request"] = [request_guard]

    async def download(self, raw, limit=None):
        self.check()
        url = media_url(raw)
        cap = min(limit or self.config().max_download_mb * 1024**2, self.config().max_download_mb * 1024**2)
        chunks = bytearray()
        connector = aiohttp.TCPConnector(resolver=PublicResolver(), use_dns_cache=False, limit=1)
        # 不带 Cookie，不跟随重定向；在实际连接的解析器里检查公网 IP。
        async with aiohttp.ClientSession(connector=connector, cookie_jar=aiohttp.DummyCookieJar(),
                                        timeout=aiohttp.ClientTimeout(total=60),
                                        headers={"Referer": "https://www.bilibili.com/", "User-Agent": "Mozilla/5.0"}) as session:
            async with session.get(url, allow_redirects=False) as response:
                if response.status != 200:
                    raise RuntimeError(f"媒体读取失败 HTTP {response.status}")
                if response.content_length and response.content_length > cap:
                    raise ValueError("媒体超过单视频下载上限")
                async for chunk in response.content.iter_chunked(65536):
                    self.check()
                    if len(chunks) + len(chunk) > cap:
                        raise ValueError("媒体超过单视频下载上限")
                    await self.store.call("reserve", self.day(), {"download_bytes": (len(chunk), self.config().daily_download_mb * 1024**2)})
                    chunks.extend(chunk)
        return bytes(chunks)

    async def metadata(self, value):
        self.check()
        bvid, page = parse_video(value)
        resource = video.Video(bvid=bvid, credential=self.credential)
        info = await self._read_stage("读取视频信息", resource.get_info())
        pages = info.get("pages", [])
        if not 1 <= page <= len(pages):
            raise ValueError("视频分 P 不存在")
        part = pages[page - 1]
        owner = info.get("owner", {})
        return {"bvid": bvid, "aid": int(info["aid"]), "page": page, "cid": int(part["cid"]),
                "title": str(info["title"])[:300], "author": str(owner.get("name", ""))[:100],
                "up_uid": str(owner.get("mid", "")), "description": str(info.get("desc", ""))[:3000],
                "duration": int(part.get("duration", info.get("duration", 0))),
                "part_title": str(part.get("part", ""))[:300], "url": f"https://www.bilibili.com/video/{bvid}?p={page}"}

    async def discover(self, query, uploader="", recommended=False):
        self.check()
        if recommended:
            return [asdict(x) for x in (await self.recommended())[:10]]
        if uploader:
            payload = await self._read_stage("读取 UP 主视频", user.User(int(uploader), credential=self.credential).get_videos(ps=10))
            rows = payload.get("list", {}).get("vlist", [])
        else:
            payload = await self._read_stage("搜索视频", search.search_by_type(query, search_type=search.SearchObjectType.VIDEO, page_size=10))
            rows = payload.get("result", [])
        return [{"bvid": row["bvid"], "title": html.unescape(re.sub(r"<[^>]+>", "", str(row.get("title", ""))))[:300],
                 "author": str(row.get("author", ""))[:100], "up_uid": str(row.get("mid", "")),
                 "description": str(row.get("description", ""))[:500]} for row in rows[:10] if isinstance(row, dict) and re.fullmatch(r"BV[0-9A-Za-z]{10}", str(row.get("bvid", "")))]

    async def subtitles(self, metadata):
        self.check()
        resource = video.Video(bvid=metadata["bvid"], credential=self.credential)
        payload = await self._read_stage("读取字幕列表", resource.get_subtitle(cid=metadata["cid"]))
        entries = sorted(payload.get("subtitles", []), key=lambda x: not str(x.get("lan", "")).startswith(("zh", "ai-zh")))
        if not entries:
            return [], "没有可访问字幕"
        content = await self.download(entries[0]["subtitle_url"], 2 * 1024**2)
        rows = []
        for row in json.loads(content).get("body", []):
            if not isinstance(row, dict) or not isinstance(row.get("content"), str):
                continue
            start, end = row.get("from"), row.get("to")
            if type(start) in (int, float) and type(end) in (int, float) and 0 <= start <= end <= metadata["duration"] + 1:
                rows.append({"from": start, "to": end, "text": row["content"][:1500]})
        _, selected = select_subtitles(rows, 7500)
        return selected, "字幕抽样；仅覆盖列出的时间范围"

    async def streams(self, metadata):
        self.check()
        payload = await self._read_stage("读取可访问媒体流", video.Video(bvid=metadata["bvid"], credential=self.credential).get_download_url(cid=metadata["cid"]))
        dash = payload.get("dash", {})
        def lowest(rows):
            candidates = sorted(rows, key=lambda row: row.get("bandwidth", 10**12))
            return candidates[0].get("baseUrl", candidates[0].get("base_url", "")) if candidates else ""
        return {"audio": lowest(dash.get("audio", [])), "video": lowest(dash.get("video", []))}

    async def thread(self, aid, root):
        self.check()
        if root:
            return await self._read_stage("读取评论线程", comment.Comment(aid, comment.CommentResourceType.VIDEO, int(root), self.credential).get_sub_comments(page_size=20))
        return await self._read_stage("读取视频评论", comment.get_comments(aid, comment.CommentResourceType.VIDEO, credential=self.credential))

    async def reconcile(self, operation, metadata):
        payload = await self.thread(metadata["aid"], operation["root"])
        rows = payload.get("replies") or []
        matches = []
        for row in rows:
            author = str((row.get("member") or {}).get("mid", ""))
            text = (row.get("content") or {}).get("message", "")
            if (author == operation["account"] and text == operation["text"]
                    and abs(float(row.get("ctime", 0)) - operation["created"]) < 600
                    and (not operation["parent"] or str(row.get("parent", "")) == operation["parent"])):
                matches.append(str(row["rpid"]))
        return matches[0] if len(matches) == 1 else None

    async def publish_operation(self, metadata, text, can_send, root="", parent=""):
        item = VideoItem(metadata["bvid"], metadata["aid"], metadata["title"], metadata["up_uid"], metadata["author"], metadata["duration"])
        return await self.publish(item, text, can_send, root=int(root) if root else None, parent=int(parent) if parent else None)

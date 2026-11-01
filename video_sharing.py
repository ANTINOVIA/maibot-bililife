"""Explicit sharing tool/API. Proactive chat decides whether to call it."""
import asyncio
import json
import time

from .bilibili_adapter import parse_video
from .security import media_url
from .storage import uid


def video_card(metadata):
    bvid, page = parse_video(metadata["url"])
    cover = str(metadata.get("cover_url", ""))
    if cover.startswith("http://"):
        cover = "https://" + cover[7:]
    cover = media_url(cover)
    return {"template_id": 37, "kv": [
        {"key": "#PROMPT#", "value": "[B站视频] " + str(metadata["title"])[:120]},
        {"key": "#METATITLE#", "value": str(metadata["title"])[:120]},
        {"key": "#METASUBTITLE#", "value": "UP 主：" + str(metadata.get("author", ""))[:100]},
        {"key": "#METACOVER#", "value": cover},
        {"key": "#METAURL#", "value": f"https://www.bilibili.com/video/{bvid}?p={page}"},
    ]}


class VideoSharing:
    def __init__(self, engine):
        self.engine, self.store = engine, engine.store
        self.lock = asyncio.Lock()

    async def recover(self):
        await self.store.call("execute", "UPDATE video_shares SET state='unknown',updated=? WHERE state='sending'", (time.time(),))

    def check(self, caller_stream_id=None):
        engine, cfg = self.engine, self.engine.config()
        if not cfg.video_share_enabled or not cfg.memory_enabled or engine.paused or engine.stopping:
            raise ValueError("视频分享已关闭、记忆未启用或任务已暂停")
        if not engine.activity_allowed():
            raise ValueError("当前处于休息或非活跃时段")
        if caller_stream_id is not None and caller_stream_id != cfg.video_share_stream_id:
            raise ValueError("只能在已配置的视频分享会话中使用此工具")
        return cfg

    async def candidates(self, caller_stream_id=None):
        cfg = self.check(caller_stream_id)
        rows = await self.store.call("rows", """SELECT v.id,v.payload,v.evidence,v.analysis FROM videos v
            WHERE v.state='understood' AND EXISTS(SELECT 1 FROM memories m WHERE m.video_id=v.id AND m.status='active')
            AND NOT EXISTS(SELECT 1 FROM video_shares s WHERE s.video_id=v.id AND s.stream_id=?)
            ORDER BY v.created DESC LIMIT 5""", (cfg.video_share_stream_id,))
        return [{"video_id": row["id"], "title": json.loads(row["payload"])["title"],
            "source": json.loads(row["payload"])["url"], "coverage": json.loads(row["evidence"]).get("coverage", ""),
            "summary": str(json.loads(row["analysis"]).get("summary", ""))[:600],
            "feeling": str(json.loads(row["analysis"]).get("feeling", ""))[:300]} for row in rows]

    def claim(self, video_id, cfg):
        now = time.time()
        day_start = self.engine.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM video_shares WHERE video_id=? AND stream_id=?", (video_id, cfg.video_share_stream_id)).fetchone()
            if existing:
                return dict(existing), False
            count = db.execute("SELECT COUNT(*) FROM video_shares WHERE stream_id=? AND created>=?", (cfg.video_share_stream_id, day_start)).fetchone()[0]
            if count >= cfg.video_share_daily_limit:
                raise ValueError("今日视频分享尝试已达上限")
            latest = db.execute("SELECT MAX(created) FROM video_shares WHERE stream_id=?", (cfg.video_share_stream_id,)).fetchone()[0]
            if latest is not None and now - latest < cfg.video_share_cooldown_seconds:
                raise ValueError("视频分享处于冷却中")
            share_id = uid()
            db.execute("INSERT INTO video_shares VALUES(?,?,?,?,?,NULL,?,?)", (share_id, video_id, cfg.video_share_stream_id, cfg.video_share_format, "sending", now, now))
            return {"id": share_id, "state": "sending"}, True

    async def share(self, video, caller_stream_id=None):
        async with self.lock:
            cfg = self.check(caller_stream_id)
            # Tool callers may use the canonical URL returned by bililife_recall.
            if isinstance(video, str) and ":p" in video and video.startswith("BV"):
                video = video.replace(":p", "?p=", 1)
                video = "https://www.bilibili.com/video/" + video
            bvid, page = parse_video(video)
            video_id = f"{bvid}:p{page}"
            rows = await self.store.call("rows", """SELECT v.payload FROM videos v WHERE v.id=? AND v.state='understood'
                AND EXISTS(SELECT 1 FROM memories m WHERE m.video_id=v.id AND m.status='active')""", (video_id,))
            if not rows:
                raise ValueError("只能分享已理解且保留真实记忆的视频")
            prior = await self.store.call("rows", "SELECT state,message_id FROM video_shares WHERE video_id=? AND stream_id=?", (video_id, cfg.video_share_stream_id))
            if prior:
                return {"state": prior[0]["state"], "sent": prior[0]["state"] == "sent", "duplicate": True, "message_id": prior[0]["message_id"]}
            streams = await asyncio.wait_for(self.engine.ctx.chat.get_all_streams(platform="all_platforms"), 10)
            streams = streams.get("streams") if isinstance(streams, dict) else streams
            target = next((row for row in streams or [] if isinstance(row, dict) and
                str(row.get("stream_id") or row.get("session_id") or "") == cfg.video_share_stream_id), None)
            if target is None or (cfg.video_share_format == "qq_ark" and target.get("platform") != "qq"):
                raise ValueError("目标会话尚未加载，或目标不是 QQ；请先在目标聊天中发送消息")
            metadata = json.loads(rows[0]["payload"])
            metadata["url"] = f"https://www.bilibili.com/video/{bvid}?p={page}"
            if cfg.video_share_format == "qq_ark" and not metadata.get("cover_url"):
                refreshed = await self.engine.platform.metadata(metadata["url"])
                metadata["cover_url"] = refreshed.get("cover_url", "")
            ark = video_card(metadata) if cfg.video_share_format == "qq_ark" else None
            current = self.check(caller_stream_id)
            if (current.video_share_stream_id, current.video_share_format) != (cfg.video_share_stream_id, cfg.video_share_format):
                raise ValueError("分享配置已变化，请重新确认")
            # Recheck memory state after network awaits before claiming an attempt.
            active = await self.store.call("rows", "SELECT id FROM memories WHERE video_id=? AND status='active' LIMIT 1", (video_id,))
            if not active:
                raise ValueError("视频记忆已遗忘，未发送")
            record, claimed = await asyncio.to_thread(self.claim, video_id, current)
            if not claimed:
                return {"state": record["state"], "sent": record["state"] == "sent", "duplicate": True, "message_id": record.get("message_id")}
            text = f"[B站视频] {metadata['title']}\n{metadata['url']}"
            state, message_id = "unknown", None
            try:
                current = self.check(caller_stream_id)
                if (current.video_share_stream_id, current.video_share_format) != (cfg.video_share_stream_id, cfg.video_share_format):
                    raise ValueError("分享目标已改变")
                if ark is not None:
                    result = await asyncio.wait_for(self.engine.ctx.send.custom("qq_ark", ark, cfg.video_share_stream_id,
                        return_details=True, processed_plain_text=text, sync_to_maisaka_history=True, maisaka_source_kind="bililife_share"), 30)
                else:
                    result = await asyncio.wait_for(self.engine.ctx.send.text(text, cfg.video_share_stream_id, return_details=True), 30)
                if isinstance(result, dict) and result.get("sent") is True:
                    state, message_id = "sent", result.get("message_id")
                elif result is True:
                    state = "sent"
            except Exception:
                pass  # The platform may have accepted it. Never retry or fall back automatically.
            await self.store.call("execute", "UPDATE video_shares SET state=?,message_id=?,updated=? WHERE id=?", (state, message_id, time.time(), record["id"]))
            await self.store.call("audit", "video_share_result", {"share_id": record["id"], "video_id": video_id, "state": state})
            return {"state": state, "sent": state == "sent", "message_id": message_id, "video_id": video_id,
                "source": metadata["url"], "notice": "结果未知时先核对聊天，不自动重发或改发文本" if state == "unknown" else ""}

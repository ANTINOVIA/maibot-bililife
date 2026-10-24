from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import asyncio
import base64
import contextlib
import json
import shutil
import time

import aiohttp
import httpx

from .bilibili_adapter import BilibiliAdapter, parse_video
from .configuration import Settings, migrate_settings
from .config_io import parse_settings_document, write_settings_file
from .daily_planner import DailyPlanner
from .comment_reading import CommentReading
from .model_diagnostics import check_model, DiagnosticError
from .interaction_service import InteractionService, PolicyBlocked
from .maibot_adapter import MaiBotAdapter, ModelUnavailable
from .memory_bridge import MemoryBridge, PersonaEvolution
from .schemas import Review, Selection
from .security import guard_unobserved_senses, redact
from .storage import BudgetExceeded, Storage, dump, uid
from .vendor.client import LoginRequired, PlatformError, VideoReadError
from .video_understanding import VideoUnderstanding


class TaskCancelled(RuntimeError):
    pass

class RestPeriod(RuntimeError):
    def __init__(self, until):
        super().__init__('处于计划休息时段，等待休息结束')
        self.until = until


class TaskExecutor:
    def __init__(self, ctx, data, runtime):
        self.ctx, self.data, self.runtime = ctx, Path(data), Path(runtime)
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.store = Storage(self.data)
        saved = self.store.get("settings", Settings().model_dump())
        config_error=""
        candidate=migrate_settings(saved)
        config_file=self.data/"settings.toml"
        if config_file.exists():
            try:
                candidate={**candidate,**parse_settings_document(config_file.read_text(encoding="utf-8"),legacy=True)}
                Settings.model_validate(candidate)
            except (ValueError,TypeError):
                candidate=migrate_settings(saved)
                config_error="settings.toml 校验失败，保留上次有效配置；请在设置总览修正后保存"
        self.settings = Settings.model_validate(candidate)
        old_settings=migrate_settings(saved)
        if config_file.exists() and not config_error:
            before={key:old_settings.get(key,Settings().model_dump()[key]) for key in ("persona_guidance","expression_preferences")}
            after={key:getattr(self.settings,key) for key in before}
            if before!=after:
                self.store.execute("INSERT INTO persona_interventions VALUES(?,?,?,?,0,?)",(uid(),dump(before),dump(after),"管理员修改服务器 settings.toml",time.time()))
            if old_settings!=self.settings.model_dump():
                self.store.audit("settings_toml_loaded",{"source":"settings.toml"})
        self.store.set("config_error",config_error)
        self.store.set("settings",self.settings.model_dump())
        if not config_error:
            write_settings_file(self.data,self.settings)
        if "mode" in saved:
            self.store.set("settings",self.settings.model_dump())
            self.store.audit("settings_migrated", {"from":"0.2.0", "to":"0.2.5", "automatic_posting_preserved":True})
        self.paused = self.store.get("paused", False)
        self.posting_stopped = self.store.get("posting_stopped", False)
        self.blocked = self.store.get("blocked", "")
        self.owner, self.current, self.cancelled = uid(), None, set()
        self.stopping, self.worker, self.running_job = False, None, None
        self.account_status, self.health = {"state": "not_checked", "uid": "", "name": ""}, {}
        self.qr = {"state": "idle", "image": None, "expires": None}
        self.host = MaiBotAdapter(ctx, self.store, self.config, self.day)
        self.platform = BilibiliAdapter(self.data, self.store, self.config, self.day)
        self.host.check = self.check
        self.platform.check = self.check
        self.understanding = VideoUnderstanding(self.platform, self.host, self.store, self.config, self.runtime)
        self.memory = MemoryBridge(self.store, self.host, self.config)
        self.evolution = PersonaEvolution(self.store, self.host, self.config)
        self.interaction = InteractionService(self)
        self.planner = DailyPlanner(self)
        self.comment_reading = CommentReading(self)

    def config(self):
        return self.settings

    def now(self):
        return datetime.now(ZoneInfo(self.settings.timezone))

    def day(self):
        return self.now().date().isoformat()

    def day_end(self):
        return (self.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp() - 0.001

    def active(self, now=None):
        value = (now or self.now()).strftime("%H:%M")
        start, end = self.settings.active_start, self.settings.active_end
        return start <= value <= end if start <= end else value >= start or value <= end

    def rest_until(self):
        now=time.time()
        rows=self.store.rows("SELECT MAX(t.deadline) AS until FROM tasks t LEFT JOIN task_tombstones d ON d.task_id=t.id WHERE t.type='rest' AND d.task_id IS NULL AND t.state NOT IN ('cancelled','skipped','expired','failed') AND t.earliest<=? AND t.deadline>?",(now,now))
        return rows[0]['until'] or 0

    def activity_allowed(self):
        return self.active() and not self.rest_until()

    def validate_query(self, query):
        if not query or len(query) > 80 or any(x in query for x in self.settings.excluded_topics):
            raise PolicyBlocked("检索词为空、过长或命中排除主题")
        if not any(x in query for x in self.settings.allowed_topics):
            raise PolicyBlocked("检索词须包含一个允许主题")

    def validate_scope(self, metadata, topic=""):
        cfg = self.settings
        text = metadata.get("title", "") + metadata.get("description", "")
        if any(x in text for x in cfg.excluded_topics):
            raise PolicyBlocked("视频命中排除主题")
        if cfg.allowed_uploaders and metadata.get("up_uid") not in cfg.allowed_uploaders:
            raise PolicyBlocked("视频不属于允许 UP 主")
        if not cfg.allowed_uploaders and topic not in cfg.allowed_topics and not any(x in text for x in cfg.allowed_topics):
            raise PolicyBlocked("视频不在允许主题范围内")

    def check(self):
        if self.stopping:
            raise TaskCancelled("插件正在卸载")
        if self.current:
            if self.current["id"] in self.cancelled or self.current["cancel_requested"]:
                raise TaskCancelled("任务已取消")
            if time.time() >= self.current["deadline"]:
                raise TaskCancelled("任务已过截止时间")
            control = self.current["type"] in ("account", "login", "reconcile", "model_test")
            if self.paused and not control:
                raise TaskCancelled("全部任务已暂停")
            if self.current['type'] in ('watch','discover','explore','read_comments','publish','replies'):
                until=self.rest_until()
                if until:
                    raise RestPeriod(until)
            if self.blocked and self.current["type"] in ("watch", "discover", "explore", "read_comments", "publish", "replies"):
                raise PolicyBlocked(self.blocked)
            self.check_module(self.current["type"])

    def check_module(self, kind):
        field={"discover":"browsing_enabled","explore":"browsing_enabled","watch":"browsing_enabled","read_comments":"browsing_enabled",
               "draft":"comments_enabled","replies":"replies_enabled","memory":"memory_enabled","review":"review_enabled","adapt":"adaptive_schedule_enabled"}.get(kind)
        if field and not getattr(self.settings,field):
            raise PolicyBlocked("此模块已关闭")

    async def start(self):
        await self.store.call("lock")
        await self.store.call("recover")
        await self.platform.load()
        self.health = {"ffmpeg": bool(shutil.which("ffmpeg")), "concurrency": 1, "host_long_term_memory": "unsupported",
                       "memory_method": "插件 SQLite 检索工具 + maisaka.context.append", "asr_enabled": self.settings.enable_asr}
        for topic in self.settings.allowed_topics:
            await self.store.call("execute", "INSERT OR IGNORE INTO interests VALUES(?,?,0)", (topic, 0.5))
        self.worker = asyncio.create_task(self.loop(), name="bililife.scheduler")
        await self.enqueue("account", {}, duration=40)

    async def stop(self):
        self.stopping = True
        if self.worker:
            self.worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.worker
        await self.platform.close()
        await self.store.call("unlock")

    async def enqueue(self, kind, payload, duration=None, earliest=None, deadline=None, dependencies=None, child=False):
        # 入队的数据只来自受控 API 或程序，不接受任意脚本、URL 请求或 shell。
        allowed = {"discover", "watch", "explore", "read_comments", "draft", "replies", "memory", "review", "rest", "account", "login", "publish", "reconcile", "plan", "adapt", "model_test"}
        if kind not in allowed:
            raise ValueError("未知任务类型")
        if len(dump(payload)) > 12000:
            raise ValueError("任务参数过大")
        if kind in ("discover", "explore", "read_comments"):
            if payload.get("query") and not payload.get("uploader") and not payload.get("recommended"):
                self.validate_query(payload.get("query", ""))
            elif payload.get("uploader") and payload["uploader"] not in self.settings.allowed_uploaders:
                raise PolicyBlocked("UP 主必须先加入允许范围")
        if kind == "watch":
            parse_video(payload.get("query", payload.get("video", "")))
        scheduled_start = earliest if earliest is not None else time.time()
        scheduled_end = deadline if deadline is not None else min(scheduled_start + self.settings.schedule_window_seconds(kind), self.day_end())
        return await self.store.call("add_task", kind, payload, self.day(), scheduled_start, scheduled_end,
                                     duration or self.settings.task_timeout_seconds,
                                     plan_id=self.current["plan_id"] if child and self.current else None,
                                     dependencies=dependencies or ([self.current["id"]] if child and self.current else []))

    async def schedule(self):
        now = self.now()
        if not self.paused:
            if self.activity_allowed() and not self.blocked and not self.posting_stopped and self.account_status["state"]=="authenticated":
                await self.interaction.queue_automatic()
            if self.settings.planner_enabled and now.strftime("%H:%M") >= self.settings.plan_time:
                plans = await self.store.call("rows", "SELECT id FROM plans WHERE day=? AND active=1", (self.day(),))
                pending = await self.store.call("rows", "SELECT id FROM tasks WHERE type='plan' AND day=? AND state IN ('queued','retry','running')", (self.day(),))
                if not plans and not pending:
                    await self.enqueue("plan", {})
            if self.settings.review_enabled and now.strftime("%H:%M") >= self.settings.review_time:
                reviews = await self.store.call("rows", "SELECT created FROM summaries WHERE day=?", (self.day(),))
                tasks = await self.store.call("rows", "SELECT id FROM tasks WHERE type='review' AND day=? AND state IN ('queued','retry','running')", (self.day(),))
                hour, minute = map(int, self.settings.review_time.split(":"))
                review_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp()
                attempts = await self.store.call("rows", "SELECT id FROM tasks WHERE type='review' AND day=? AND created>=?", (self.day(), review_at))
                if (not reviews or reviews[0]["created"] < review_at) and not tasks and not attempts:
                    await self.enqueue("review", {})
            next_reply = await self.store.call("get", "next_reply", 0)
            if self.settings.replies_enabled and self.activity_allowed() and not self.blocked and self.account_status["state"] == "authenticated" and time.time() >= next_reply:
                await self.enqueue("replies", {})
                await self.store.call("set", "next_reply", time.time() + self.settings.reply_interval_seconds)
        if time.time() >= await self.store.call("get", "next_cleanup", 0):
            await self.store.call("prune", self.settings.retention_days, self.runtime, self.settings.cache_hours)
            await self.store.call("set", "next_cleanup", time.time() + 86400)

    async def loop(self):
        while True:
            try:
                await self.schedule()
                task = await self.store.call("claim", self.owner, self.paused, self.activity_allowed())
                if task:
                    if (self.paused and task["type"] not in ("login", "account", "reconcile", "model_test")) or (not self.activity_allowed() and task["type"] in ("discover", "watch", "explore", "read_comments", "publish", "replies")):
                        await self.store.call("finish", task, "retry", error="暂停或休息时段", next_run=time.time() + 60)
                    else:
                        await self.run(task)
                else:
                    await asyncio.sleep(3)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.store.call("audit", "scheduler_error", {"error": type(exc).__name__})
                await asyncio.sleep(5)

    async def run(self, task):
        self.current = task
        try:
            self.check()
            size = await asyncio.to_thread(lambda: sum(p.stat().st_size for p in [*self.data.glob("*.sqlite3*"), *self.runtime.glob("*")] if p.is_file()))
            if size > self.settings.max_storage_mb * 1024**2 and task["type"] not in ("account", "login", "reconcile", "rest", "model_test"):
                raise BudgetExceeded("插件存储额度耗尽")
            timeout = min(task["duration"], max(1, task["deadline"] - time.time()))
            self.running_job = asyncio.create_task(self.dispatch(task["type"], json.loads(task["payload"])))
            result = await asyncio.wait_for(self.running_job, timeout)
            self.check()
            await self.store.call("finish", task, "unknown" if isinstance(result, dict) and result.get("status") == "unknown" else "succeeded", result)
        except asyncio.CancelledError:
            state = "unknown" if task["type"] == "publish" else "retry" if self.stopping else "cancelled" if task["id"] in self.cancelled else "paused"
            await asyncio.shield(self.store.call("finish", task, state, error="插件卸载或操作中断", next_run=time.time() + 10))
            if self.stopping:
                raise
        except TaskCancelled as exc:
            state = "cancelled" if task["id"] in self.cancelled else "paused" if self.paused else "expired"
            await self.store.call("finish", task, state, error=str(exc))
        except RestPeriod as exc:
            await self.store.call('finish',task,'retry',error=str(exc),next_run=exc.until)
        except BudgetExceeded as exc:
            await self.store.call("finish", task, "budget_exhausted", error=str(exc))
        except (LoginRequired, PlatformError) as exc:
            restricted = isinstance(exc, LoginRequired) or exc.code in (-101, -111, -352, -412, 401, 403, 412, 429)
            if restricted:
                self.blocked = "账号失效或平台限制，请检查登录/验证码/权限后恢复"
                self.account_status["state"] = "restricted"
                await self.store.call("set", "blocked", self.blocked)
            await self.store.call("finish", task, "blocked_login" if restricted else "failed", error=str(exc))
        except PolicyBlocked as exc:
            await self.store.call("finish", task, "blocked_policy", error=str(exc))
        except DiagnosticError as exc:
            await self.store.call("finish", task, "failed", error=exc.code+'：'+str(exc))
        except ModelUnavailable as exc:
            await self.store.call("finish", task, "blocked_model", error=str(exc))
        except (VideoReadError, httpx.TransportError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            retryable = not isinstance(exc, VideoReadError) or exc.retryable
            state = "retry" if retryable and task["type"] != "publish" and task["attempts"] <= self.settings.max_retries else "timeout" if isinstance(exc, asyncio.TimeoutError) else "failed"
            await self.store.call("finish", task, state, error=type(exc).__name__, next_run=time.time() + min(300, 15 * 2**task["attempts"]))
        except Exception as exc:
            detail = redact(str(exc))[:300] if type(exc) is ValueError else "执行失败，查看脱敏审计"
            await self.store.call("finish", task, "failed", error=type(exc).__name__ + "：" + detail)
            await self.store.call("audit", "task_error", {"task_id": task["id"], "stage": task["type"], "error": type(exc).__name__})
        finally:
            if task["type"] == "watch":
                state = await self.store.call("rows", "SELECT state,error FROM tasks WHERE id=?", (task["id"],))
                if state and state[0]["state"] != "succeeded":
                    payload = json.loads(task["payload"])
                    try:
                        bvid, page = parse_video(payload.get("query", payload.get("video", "")))
                    except ValueError:
                        pass
                    else:
                        await self.store.call("execute", "UPDATE videos SET state=?,reason=? WHERE bvid=? AND page=? AND state IN ('reading','analyzing')",
                                              (state[0]["state"], state[0]["error"] or "理解未完成", bvid, page))
            self.current = None
            self.running_job = None

    async def dispatch(self, kind, payload):
        self.check()
        self.check_module(kind)
        if kind == "rest":
            return {"rested": True, "reason": payload.get("reason", "计划休息")}
        if kind == "model_test":
            return await self.model_test(payload["role"])
        if kind == "read_comments":
            return await self.comment_reading.read(payload)
        if kind == "account":
            if self.platform.credential is None:
                self.account_status = {"state": "not_logged_in", "uid": "", "name": ""}
                return self.account_status
            await self.platform.check_account()
            self.account_status = {"state": "authenticated", "uid": self.platform.uid, "name": self.platform.name}
            return self.account_status
        if kind == "login":
            image, key = await self.platform.begin_login()
            self.qr = {"state": "waiting", "image": "data:image/png;base64," + base64.b64encode(image).decode(), "expires": time.time() + 180}
            try:
                result = await self.platform.poll_login(key)
                self.qr = {"state": "authenticated", "image": None, "expires": None}
                self.account_status = {"state": "authenticated", "uid": self.platform.uid, "name": self.platform.name}
                return {"result": result}
            except BaseException:
                self.qr = {"state": "expired_or_failed", "image": None, "expires": None}
                raise
        if kind == "plan":
            return await self.planner.create(bool(payload.get("revise")), payload.get("reason", "每日主计划"))
        if kind == "adapt":
            return await self.planner.adapt(payload["video_id"])
        if kind in ("discover", "explore"):
            return await self.discover(payload)
        if kind == "watch":
            return await self.watch(payload)
        if kind == "draft":
            return await self.interaction.create_draft(await self.resolve_video(payload))
        if kind == "memory":
            if payload.get("video_id") or payload.get("query"):
                return await self.memory.extract(await self.resolve_video(payload))
            rows = await self.store.call("rows", "SELECT v.id FROM videos v LEFT JOIN memories m ON m.video_id=v.id WHERE v.state='understood' AND m.id IS NULL ORDER BY v.created DESC LIMIT 3")
            return [await self.memory.extract(x["id"]) for x in rows]
        if kind == "publish":
            return await self.interaction.publish(payload["draft_id"], bool(payload.get("manual")))
        if kind == "replies":
            return await self.interaction.replies()
        if kind == "reconcile":
            return await self.interaction.reconcile()
        if kind == "review":
            return await self.review()
        raise ValueError("未知任务类型")

    async def model_test(self, role):
        return await check_model(self,role)

    async def resolve_video(self, payload):
        if payload.get("video_id"):
            return payload["video_id"]
        query = payload.get("query", "")
        if query.startswith("BV"):
            rows = await self.store.call("rows", "SELECT id FROM videos WHERE bvid=? AND state='understood'", (query,))
        else:
            rows = await self.store.call("rows", "SELECT id FROM videos WHERE state='understood' ORDER BY created DESC LIMIT 1")
        if not rows:
            raise ValueError("没有可用的理解记录")
        return rows[0]["id"]

    async def discover(self, payload):
        if not payload.get("query") and not payload.get("uploader") and not payload.get("recommended"):
            from .schemas import DiscoveryChoice
            choice=await self.host.generate("discover",{
                "interests":await self.store.call("rows","SELECT * FROM interests ORDER BY weight DESC LIMIT 20"),
                "allowed_topics":self.settings.allowed_topics,"excluded_topics":self.settings.excluded_topics,
                "recent":await self.store.call("rows","SELECT bvid,analysis FROM videos ORDER BY created DESC LIMIT 3")},DiscoveryChoice)
            self.validate_query(choice.query)
            payload={**payload,"query":choice.query,"reason":choice.reason}
        rows = await self.platform.discover(payload.get("query", ""), payload.get("uploader", ""), payload.get("recommended", False))
        chosen, skipped = [], []
        for item in rows[:min(8, self.settings.max_videos * 2)]:
            self.check()
            old = await self.store.call("rows", "SELECT id FROM videos WHERE bvid=?", (item["bvid"],))
            if old:
                skipped.append({"bvid": item["bvid"], "reason": "重复内容"})
                continue
            pending=await self.store.call("rows","SELECT id FROM tasks WHERE type='watch' AND json_extract(payload,'$.query')=? AND state IN ('queued','retry','running')",(item["bvid"],))
            if pending:
                skipped.append({"bvid":item["bvid"],"reason":"已在观看队列"})
                continue
            decision = await self.host.generate("select", {"candidate": item, "interests": await self.store.call("rows", "SELECT * FROM interests"),
                                                           "allowed_topics": self.settings.allowed_topics, "excluded_topics": self.settings.excluded_topics}, Selection)
            if decision.browse:
                try:
                    self.validate_scope(item, decision.topic)
                except PolicyBlocked:
                    skipped.append({"bvid": item["bvid"], "reason": "超出范围"})
                    continue
                start = time.time() + len(chosen) * (self.settings.schedule_task_minutes + self.settings.schedule_gap_minutes) * 60
                deadline = self.life_deadline(start)
                if deadline-start < 30:
                    skipped.append({"bvid": item["bvid"], "reason": "今天剩余活跃时段不足以安排短时观看"})
                    continue
                task_id = await self.enqueue("watch", {"query": item["bvid"], "topic": decision.topic}, earliest=start, deadline=deadline, child=True)
                chosen.append({"bvid": item["bvid"], "task_id": task_id, "reason": decision.reason})
                if len(chosen) >= min(payload.get("max_items", 1), self.settings.max_videos):
                    break
            else:
                skipped.append({"bvid": item["bvid"], "reason": decision.reason})
        await self.store.call("audit", "discovery", {"chosen": chosen, "skipped": skipped})
        return {"query":payload.get("query",""),"reason":payload.get("reason",""),"chosen": chosen, "skipped": skipped}

    def life_deadline(self, start=None):
        hour,minute=map(int,self.settings.active_end.split(":"))
        end=self.now().replace(hour=hour,minute=minute,second=59,microsecond=0).timestamp()
        return min((start if start is not None else time.time()) + self.settings.schedule_window_seconds('watch'),
                   self.day_end(),end if end>time.time() else self.day_end())

    async def watch(self, payload):
        metadata = await self.platform.metadata(payload.get("query", payload.get("video", "")))
        video_id = f"{metadata['bvid']}:p{metadata['page']}"
        existing = await self.store.call("rows", "SELECT state FROM videos WHERE id=?", (video_id,))
        if existing and existing[0]["state"] == "understood":
            return {"video_id": video_id, "duplicate": True}
        selection = await self.host.generate("select", {"candidate": metadata, "interests": await self.store.call("rows", "SELECT * FROM interests"),
                                                       "allowed_topics": self.settings.allowed_topics, "excluded_topics": self.settings.excluded_topics}, Selection)
        reason = selection.reason
        try:
            self.validate_scope(metadata, selection.topic)
        except PolicyBlocked as exc:
            selection.browse, reason = False, str(exc)
        if metadata["duration"] > self.settings.max_video_seconds:
            selection.browse, reason = False, "视频超过允许时长；本次跳过"
        await self.store.call("execute", "INSERT INTO videos VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,reason=excluded.reason",
                              (video_id, metadata["bvid"], metadata["page"], dump(metadata), "{}", "{}", "reading" if selection.browse else "skipped", reason, time.time()))
        if not selection.browse:
            return {"video_id": video_id, "skipped": True, "reason": reason}
        await self.store.call("reserve_once", self.day(), "watch:" + video_id,
                              {"videos": (1, self.settings.max_videos), "browse_seconds": (metadata["duration"], self.settings.max_browse_seconds)})
        evidence, analysis = await self.understanding.analyze(metadata, video_id)
        analysis["topic"] = selection.topic
        await self.store.call("execute", "UPDATE videos SET evidence=?,analysis=?,state='understood' WHERE id=?", (dump(evidence), dump(analysis), video_id))
        self.check()
        if self.settings.comments_enabled:
            await self.enqueue("draft", {"video_id": video_id}, child=True)
        if self.settings.memory_enabled:
            await self.enqueue("memory", {"video_id": video_id}, child=True)
        if self.settings.adaptive_schedule_enabled:
            await self.enqueue("adapt", {"video_id": video_id}, deadline=self.life_deadline(), child=True)
        return {"video_id": video_id, "coverage": evidence["coverage"], "analysis": analysis}

    async def review(self):
        day = self.day()
        old = await self.store.call("rows", "SELECT payload FROM summaries WHERE day=?", (day,))
        start = self.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        data = {}
        for key, sql in {
            "interactions": "SELECT * FROM operations WHERE created>=?",
            "memories": "SELECT * FROM memories WHERE created>=? OR updated>=?",
            "tasks": "SELECT type,state,payload,result,error FROM tasks WHERE created>=?",
            "memory_changes": "SELECT kind,data,created FROM audit WHERE created>=? AND kind LIKE 'memory_%'"}.items():
            data[key] = await self.store.call("rows", sql + " LIMIT 50", (start, start) if key == "memories" else (start,))
        # 跨日读取保留原任务归属，但复盘按实际成功完成日纳入视频，不能漏掉午夜完成的理解。
        data["videos"] = await self.store.call("rows", "SELECT * FROM videos WHERE created>=? OR id IN (SELECT json_extract(result,'$.video_id') FROM tasks WHERE type='watch' AND state='succeeded' AND updated>=?) LIMIT 50", (start,start))
        data["comment_reading"] = await self.store.call("rows", "SELECT * FROM comment_reads WHERE created>=? LIMIT 30", (start,))
        data["budget"] = await self.store.call("rows", "SELECT * FROM budget WHERE day=?", (day,))
        records = [{"id": x["id"], "bvid": x["bvid"], "analysis": json.loads(x["analysis"]), "url": json.loads(x["payload"])["url"]} for x in data["videos"] if x["state"] == "understood"]
        data["interest_changes"] = await self.evolution.evolve(day, records)
        data["storage_bytes"] = await asyncio.to_thread(lambda: self.store.path.stat().st_size)
        model_data = {**data, "videos": [{"id": row["id"], "state": row["state"], "reason": row["reason"],
                                         "source": json.loads(row["payload"])["url"], "analysis": json.loads(row["analysis"]),
                                         "coverage": json.loads(row["evidence"]).get("coverage", "尚未理解")} for row in data["videos"]],
                      "tasks": [{"type": row["type"], "state": row["state"], "error": row["error"]} for row in data["tasks"]]}
        model_data['comment_reading']=[{'id':row['id'],'state':row['state'],'source':json.loads(row['metadata']).get('url'),
            'coverage':'仅阅读评论，未观看视频','summary':json.loads(row['analysis']).get('summary','')[:800]} for row in data['comment_reading'][:8]]
        evidence = {"types": list({kind for row in data["videos"] for kind in json.loads(row["evidence"]).get("types",[])})}
        validation_error = ""
        for attempt in range(2):
            summary = await self.host.generate("review", model_data, Review)
            try:
                for statement in [summary.summary,*summary.learned,*summary.unresolved,*summary.tomorrow]:
                    guard_unobserved_senses(statement, evidence)
                break
            except ValueError as exc:
                if attempt:
                    validation_error = str(exc)
                    # 模型反复作出无证据判断时，用可核对的执行记录生成保守摘要，保留降级原因。
                    understood = [row for row in data["videos"] if row["state"] == "understood"]
                    published = [row for row in data["interactions"] if row["state"] == "succeeded"]
                    summary = Review(summary=f"模型复盘证据校验未通过；以下仅汇总执行记录：理解记录 {len(understood)} 条，只读评论 {sum(row['state']=='understood' for row in data['comment_reading'])} 次，成功发布 {len(published)} 条，记忆记录 {len(data['memories'])} 条。未执行任务不计为经历。",
                                     learned=[json.loads(row["payload"])["url"] + "：" + json.loads(row["evidence"]).get("coverage","未取得内容证据") for row in understood[:8]],
                                     unresolved=["模型复盘未通过证据校验："+validation_error],
                                     tomorrow=["依据未完成任务和资源预算重新规划，不自动补发评论。"])
                    await self.store.call("audit", "review_degraded", {"day":day,"error":validation_error})
                    break
                model_data["validation_feedback"] = str(exc) + "。只记录异常和待核实问题，不保留无证据判断。"
        version = json.loads(old[0]["payload"]).get("version",1) + 1 if old else 1
        if old:
            await self.store.call("audit", "review_revised", {"day":day,"previous":json.loads(old[0]["payload"]),"version":version})
        await self.store.call("execute", "INSERT INTO summaries VALUES(?,?,?,?) ON CONFLICT(day) DO UPDATE SET payload=excluded.payload,text=excluded.text,created=excluded.created", (day, dump({"version":version,"degraded":bool(validation_error),"validation_error":validation_error,"review": summary.model_dump(), "records": data}), summary.summary, time.time()))
        if self.settings.summary_stream_id:
            self.check()
            await self.ctx.send.text(summary.summary, self.settings.summary_stream_id)
        return {"day": day, "summary": summary.model_dump(), "degraded":bool(validation_error),"validation_error":validation_error}

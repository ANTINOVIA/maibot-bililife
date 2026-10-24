from dataclasses import asdict

import asyncio
import json
import time

from .schemas import Draft, InteractionDecision
from .security import guard_unobserved_senses, normalized, safe_public_text, similar
from .storage import dump, uid
from .vendor.client import LoginRequired, PlatformError, PublishNotSent, UnknownPublish
from .video_understanding import evidence_text


class PolicyBlocked(RuntimeError):
    pass


class InteractionService:
    def __init__(self, engine):
        self.engine = engine
        self.store, self.host, self.platform = engine.store, engine.host, engine.platform

    @staticmethod
    def style_matches(text, config):
        body = normalized(text)
        return [phrase for phrase in config.comment_avoid_phrases
                if normalized(phrase) and normalized(phrase) in body]

    async def create_draft(self, video_id, incoming=None):
        cfg = self.engine.config()
        if not (cfg.replies_enabled if incoming else cfg.comments_enabled):
            return {"drafted": False, "reason": "对应互动模块已关闭"}
        # 自动草稿绑定当前真实账号，避免启动时尚未检查账号产生匿名草稿。
        await self.platform.check_account()
        rows = await self.store.call("rows", "SELECT * FROM videos WHERE id=?", (video_id,))
        if not rows or rows[0]["state"] != "understood":
            return {"drafted": False, "reason": "尚无真实理解记录"}
        row = rows[0]
        metadata, evidence, analysis = [json.loads(row[x]) for x in ("payload", "evidence", "analysis")]
        if not analysis.get("suitable_for_comment") or evidence.get("types") == ["metadata"]:
            return {"drafted": False, "reason": "证据不足或不适合互动"}
        comment_context=await self.platform.comment_context(metadata["aid"],incoming["root_rpid"] if incoming else "")
        context = {"video": metadata, "evidence": evidence, "understanding": analysis, "thread": incoming or {},
                   "comments":comment_context,"allowed_topics": cfg.allowed_topics}
        decision = await self.host.generate("decide", context, InteractionDecision)
        if not decision.interact:
            await self.store.call("audit", "silent", {"video_id": video_id, "reason": decision.reason})
            return {"drafted": False, "reason": decision.reason}
        context["decision"] = decision.model_dump()
        for attempt in range(2):
            draft = await self.host.generate("reply" if incoming else "comment", context, Draft)
            matches = self.style_matches(draft.text, self.engine.config())
            if not matches:
                break
            await self.store.call("audit", "comment_style_repair", {"video_id": video_id, "attempt": attempt + 1, "matched_phrases": matches})
            if attempt:
                raise PolicyBlocked("评论两次包含配置中避免使用的表达，未保存或发布")
            context["style_feedback"] = {"previous_draft": draft.model_dump(), "avoid_phrases": matches,
                                         "instruction": "按管理员风格要求重新写，直接回应一个具体内容，避免助手汇报和套话；不要仅通过插入标点或空格规避表达检查。保留真实来源引用。"}
        text = evidence_text(metadata, evidence) + ("\n" + incoming["text"] if incoming else "")
        discussion=text+'\n'+'\n'.join(row['text'] for row in comment_context)
        if not draft.evidence_quote or draft.evidence_quote not in text or decision.evidence_quote not in discussion:
            raise ValueError("草稿缺少可追溯的逐字证据")
        safe_public_text(draft.text)
        guard_unobserved_senses(draft.text, evidence)
        mention=None
        if not incoming and cfg.mention_owner and cfg.owner_uid and cfg.owner_uid!=self.platform.uid:
            draft.text,mention=await self.platform.owner_mention(draft.text,cfg.owner_uid)
            safe_public_text(draft.text)
        draft_id, now = uid(), time.time()
        await self.store.call("execute", "INSERT INTO drafts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              (draft_id, video_id, "reply" if incoming else "comment", draft.text, draft.evidence_quote, draft.reason, "draft", self.platform.uid,
                               str(incoming["root_rpid"]) if incoming else "", str(incoming["source_rpid"]) if incoming else "",
                               incoming["sender_uid"] if incoming else "", incoming["event_id"] if incoming else None, now, now))
        await self.store.call("execute","INSERT INTO draft_origins VALUES(?,'agent')",(draft_id,))
        if mention:
            await self.store.call("execute","INSERT INTO draft_mentions VALUES(?,?,?)",(draft_id,mention["uid"],mention["name"]))
        if (cfg.auto_publish_replies if incoming else cfg.auto_publish_comments):
            await self.queue_automatic()
        return {"drafted": True, "draft_id": draft_id}

    async def queue_automatic(self):
        cfg=self.engine.config()
        if self.engine.paused or self.engine.posting_stopped or self.engine.blocked or not self.engine.activity_allowed():
            return []
        if not (cfg.auto_publish_comments or cfg.auto_publish_replies):
            return []
        rows=await self.store.call("rows","SELECT d.* FROM drafts d JOIN draft_origins o ON o.draft_id=d.id WHERE d.status='draft' AND d.account=? AND o.origin='agent' ORDER BY d.created LIMIT 20",(self.platform.uid,))
        queued=[]
        for row in rows:
            if not (cfg.auto_publish_replies if row['kind']=='reply' else cfg.auto_publish_comments):
                continue
            old=await self.store.call("rows","SELECT id FROM tasks WHERE type='publish' AND json_extract(payload,'$.draft_id')=?",(row['id'],))
            if old:
                continue
            queued.append(await self.engine.enqueue('publish',{'draft_id':row['id'],'manual':False},deadline=self.engine.life_deadline()))
        return queued

    async def publish(self, draft_id, manual=False):
        self.engine.check()
        cfg = self.engine.config()
        if self.engine.paused or self.engine.posting_stopped or not self.engine.activity_allowed():
            raise PolicyBlocked("发布已停止或不在允许时段")
        await self.platform.check_account()
        rows = await self.store.call("rows", "SELECT * FROM drafts WHERE id=?", (draft_id,))
        if not rows or rows[0]["status"] != "draft":
            raise PolicyBlocked("草稿不存在或已进入发送流程")
        draft = rows[0]
        mentions=await self.store.call("rows","SELECT uid,name FROM draft_mentions WHERE draft_id=?",(draft_id,))
        style_text = draft["text"]
        for mention in mentions:
            style_text = style_text.replace('@' + mention['name'] + ' ', '')
        if self.style_matches(style_text, self.engine.config()):
            raise PolicyBlocked("评论包含配置中避免使用的表达，请修改正文或调整评论设置")
        if mentions and (not cfg.mention_owner or any(row['uid']!=cfg.owner_uid for row in mentions)):
            raise PolicyBlocked("主人提醒配置已改变，请重新生成草稿")
        enabled = cfg.replies_enabled if draft["kind"]=="reply" else cfg.comments_enabled
        automatic = cfg.auto_publish_replies if draft["kind"]=="reply" else cfg.auto_publish_comments
        if not enabled or (not manual and not automatic):
            raise PolicyBlocked("对应模块或自动发布开关未启用")
        if draft["account"] and draft["account"] != self.platform.uid:
            raise PolicyBlocked("草稿账号与当前账号不同，请重新生成")
        if not draft["account"]:
            if not manual:
                raise PolicyBlocked("匿名草稿须由用户明确使用当前账号提交")
            draft["account"] = self.platform.uid
        if draft["target_uid"] == self.platform.uid:
            raise PolicyBlocked("禁止回复自己")
        videos = await self.store.call("rows", "SELECT * FROM videos WHERE id=?", (draft["video_id"],))
        if not videos:
            raise PolicyBlocked("视频记录不存在")
        record = videos[0]
        metadata, evidence, analysis = [json.loads(record[x]) for x in ("payload", "evidence", "analysis")]
        if (not analysis.get("context_sufficient", analysis.get("suitable_for_comment"))
                or (not manual and not analysis.get("suitable_for_comment")) or evidence.get("types") == ["metadata"]):
            raise PolicyBlocked("没有足够内容证据")
        self.engine.validate_scope(metadata, json.loads(record["analysis"]).get("topic", ""))
        safe_public_text(draft["text"])
        old = await self.store.call("rows", "SELECT text FROM operations WHERE account=? AND state IN ('pending','sending','unknown','succeeded') ORDER BY created DESC LIMIT 2000", (self.platform.uid,))
        if any(similar(draft["text"], x["text"]) for x in old):
            raise PolicyBlocked("与已有或结果未知的发言重复/近似重复")
        operation_id, now, day = uid(), time.time(), self.engine.day()
        def prepare():
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                unresolved = db.execute("SELECT id FROM operations WHERE account=? AND state IN ('pending','sending','unknown')", (self.platform.uid,)).fetchone()
                if unresolved:
                    raise PolicyBlocked("存在未核对的发布操作")
                operations = db.execute("SELECT * FROM operations WHERE account=? AND state IN ('pending','sending','unknown','succeeded')", (self.platform.uid,)).fetchall()
                today = [x for x in operations if x["day"] == day and x["kind"] == draft["kind"]]
                limit = cfg.max_replies if draft["kind"] == "reply" else cfg.max_comments
                if len(today) >= limit:
                    raise PolicyBlocked("当日互动额度耗尽")
                same_video = [x for x in operations if x["video_id"] == draft["video_id"]]
                if draft["kind"] == "comment" and sum(x["kind"] == "comment" for x in same_video) >= cfg.max_per_video:
                    raise PolicyBlocked("同视频评论额度耗尽")
                if any(now - x["created"] < cfg.cooldown_seconds for x in same_video):
                    raise PolicyBlocked("同视频冷却中")
                if draft["target_uid"] and any(x["target_uid"] == draft["target_uid"] and now - x["created"] < cfg.user_cooldown_seconds for x in operations):
                    raise PolicyBlocked("同用户冷却中")
                if draft["root"] and sum(x["root"] == draft["root"] for x in operations) >= cfg.max_thread_rounds:
                    raise PolicyBlocked("线程跟进轮数已达上限")
                if draft["root"] and not any(x["rpid"] in (draft["root"], draft["parent"]) or x["root"] == draft["root"] for x in operations if x["state"] == "succeeded"):
                    raise PolicyBlocked("线程不是本账号已发布评论的关联线程")
                if db.execute("SELECT status FROM drafts WHERE id=?", (draft_id,)).fetchone()[0] != "draft":
                    raise PolicyBlocked("草稿已被提交")
                db.execute("INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (operation_id, draft_id, "pending", self.platform.uid, day, draft["video_id"], draft["kind"], draft["root"], draft["parent"], draft["target_uid"], draft["text"], None, None, now, now))
                db.execute("UPDATE drafts SET status='pending',account=?,updated=? WHERE id=?", (self.platform.uid, now, draft_id))
        await asyncio.to_thread(prepare)
        # 紧邻实际 HTTP POST 的回调再次检查取消、模式与停发开关。
        def can_send():
            try:
                self.engine.check()
            except Exception:
                return False
            current = self.engine.config()
            return (not self.engine.paused and not self.engine.posting_stopped and self.engine.activity_allowed() and self.platform.uid == draft["account"]
                    and not self.style_matches(style_text, current)
                    and (not mentions or (current.mention_owner and all(row['uid']==current.owner_uid for row in mentions)))
                    and (current.replies_enabled if draft["kind"]=="reply" else current.comments_enabled)
                    and (manual or (current.auto_publish_replies if draft["kind"]=="reply" else current.auto_publish_comments)))
        try:
            await self.store.call("execute", "UPDATE operations SET state='sending',updated=? WHERE id=?", (time.time(), operation_id))
            if mentions:
                mapping={row['name']:int(row['uid']) for row in mentions if '@'+row['name']+' ' in draft['text']}
                rpid = await self.platform.publish_operation(metadata, draft["text"], can_send, draft["root"], draft["parent"],mentions=mapping)
            else:
                rpid = await self.platform.publish_operation(metadata, draft["text"], can_send, draft["root"], draft["parent"])
        except (PublishNotSent, LoginRequired) as exc:
            await self._result(operation_id, draft_id, "not_sent", type(exc).__name__)
            raise
        except PlatformError as exc:
            await self._result(operation_id, draft_id, "rejected", f"平台 code={exc.code}")
            raise
        except (UnknownPublish, asyncio.CancelledError, Exception) as exc:
            await asyncio.shield(self._result(operation_id, draft_id, "unknown", "结果未知，禁止自动重发"))
            if isinstance(exc, asyncio.CancelledError):
                raise
            return {"operation_id": operation_id, "status": "unknown"}
        await self._result(operation_id, draft_id, "succeeded", None, rpid)
        return {"operation_id": operation_id, "status": "succeeded", "rpid": rpid}

    async def _result(self, operation_id, draft_id, state, error=None, rpid=None):
        def update():
            with self.store.connect() as db:
                db.execute("UPDATE operations SET state=?,error=?,rpid=COALESCE(?,rpid),updated=? WHERE id=?", (state, error, rpid, time.time(), operation_id))
                db.execute("UPDATE drafts SET status=?,updated=? WHERE id=?", (state, time.time(), draft_id))
        await asyncio.to_thread(update)

    async def reconcile(self):
        rows = await self.store.call("rows", "SELECT o.*,v.payload FROM operations o JOIN videos v ON v.id=o.video_id WHERE o.state='unknown' ORDER BY o.created LIMIT 10")
        results = []
        for row in rows:
            if row["account"] != self.platform.uid:
                continue
            found = await self.platform.reconcile(row, json.loads(row["payload"]))
            if found:
                await self._result(row["id"], row["draft_id"], "succeeded", None, found)
            results.append({"operation_id": row["id"], "status": "succeeded" if found else "unknown"})
        return results

    async def replies(self):
        if not self.engine.config().replies_enabled:
            raise PolicyBlocked("回复模块已关闭")
        await self.platform.check_account()
        await self.reconcile()
        rows = await self.store.call("rows", "SELECT o.*,v.payload FROM operations o JOIN videos v ON v.id=o.video_id WHERE o.state='succeeded' AND o.account=?", (self.platform.uid,))
        own = {}
        by_aid = {}
        for row in rows:
            metadata = json.loads(row["payload"])
            root_id = row["root"] or row["rpid"]
            root_text = next((x["text"] for x in rows if x["rpid"] == root_id), row["text"])
            own[row["rpid"]] = {"aid": metadata["aid"], "bvid": metadata["bvid"], "root_rpid": int(root_id), "root_text": root_text, "text": row["text"]}
            by_aid[metadata["aid"]] = row["video_id"]
        since = await self.store.call("get", "reply_since", time.time() - 86400)
        incoming = await self.platform.incoming_replies(own, since)
        results = []
        for message in incoming:
            self.engine.check()
            if message.sender_uid == self.platform.uid:
                continue
            event_id = f"{self.platform.uid}:{message.source_rpid}"
            payload = {**asdict(message), "event_id": event_id}
            await self.store.call("execute", "INSERT OR IGNORE INTO reply_events VALUES(?,?,?,?)", (event_id, dump(payload), "received", time.time()))
        pending = await self.store.call("rows", "SELECT id,payload FROM reply_events WHERE state='received' ORDER BY created LIMIT 20")
        for event in pending:
            self.engine.check()
            payload = json.loads(event["payload"])
            event_id = event["id"]
            if not event_id.startswith(self.platform.uid + ":") or payload["aid"] not in by_aid:
                continue
            existing = await self.store.call("rows", "SELECT id FROM drafts WHERE event_id=?", (event_id,))
            if existing:
                results.append({"draft_id": existing[0]["id"], "existing": True})
            else:
                results.append(await self.create_draft(by_aid[payload["aid"]], payload))
            await self.store.call("execute", "UPDATE reply_events SET state='processed' WHERE id=?", (event_id,))
        await self.store.call("set", "reply_since", time.time() - 300)
        return results

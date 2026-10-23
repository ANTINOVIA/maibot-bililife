from datetime import datetime
from zoneinfo import ZoneInfo
import asyncio
import json
import time

from .configuration import Settings
from .security import redact, safe_public_text
from .storage import uid
from .storage import dump
from .module_settings import MODULES
from .config_io import parse_settings_document, settings_document, write_settings_file


class WebUIService:
    def __init__(self, engine):
        self.engine, self.store = engine, engine.store
        self.settings_lock=asyncio.Lock()

    async def snapshot(self, section="overview", page=1):
        page = max(1, min(int(page), 1000))
        engine = self.engine
        if section == "overview":
            return {"account": engine.account_status, "modules":{key:getattr(engine.config(),key) for key in ("planner_enabled","browsing_enabled","comments_enabled","replies_enabled","auto_publish_comments","auto_publish_replies","memory_enabled","evolution_enabled","review_enabled")}, "paused": engine.paused,
                    "posting_stopped": engine.posting_stopped, "blocked": engine.blocked, "timezone": engine.config().timezone,
                    "current_task": {k: engine.current[k] for k in ("id", "type", "day", "deadline")} if engine.current else None,
                    "budget": await self.store.call("rows", "SELECT * FROM budget WHERE day=?", (engine.day(),)),
                    "progress": await self.store.call("rows", "SELECT state,COUNT(*) AS count FROM tasks WHERE day=? GROUP BY state", (engine.day(),)),
                    "health": engine.health, "plan_error": await self.store.call("get", "plan_error", ""),
                    "config_error":await self.store.call("get","config_error",""),
                    "summaries": await self.store.call("rows", "SELECT day,text FROM summaries ORDER BY day DESC LIMIT 3")}
        if section == "settings":
            return {"settings": engine.config().model_dump(), "schema": Settings.model_json_schema(), "account": engine.account_status,"modules":MODULES,"revision":await self.store.call("get","settings_revision",0),"paused":engine.paused,"posting_stopped":engine.posting_stopped}
        if section == "schedule_board" or section.startswith("schedule_board:"):
            day=section.split(":",1)[1] if ":" in section else engine.day()
            chosen=datetime.fromisoformat(day)
            if chosen.strftime("%Y-%m-%d")!=day or abs((chosen.date()-engine.now().date()).days)>365:
                raise ValueError("日程日期超出可查询范围")
            rows=await self.store.call("rows","SELECT t.* FROM tasks t LEFT JOIN task_tombstones d ON d.task_id=t.id WHERE t.day=? AND d.task_id IS NULL ORDER BY t.earliest LIMIT 500",(day,))
            deleted=await self.store.call("rows","SELECT t.id,t.type,t.state,d.reason,d.created FROM task_tombstones d JOIN tasks t ON t.id=d.task_id WHERE t.day=? ORDER BY d.created DESC LIMIT 20",(day,))
            plans=await self.store.call("rows","SELECT id,version,payload,reason FROM plans WHERE day=? AND active=1",(day,))
            for row in rows:
                row["payload"]=json.loads(row["payload"])
            adaptations=await self.store.call("rows","SELECT video_id,state,payload,created FROM adaptive_decisions WHERE day=? ORDER BY created DESC LIMIT 10",(day,))
            for item in adaptations:
                item["payload"]=json.loads(item["payload"])
            return redact({"date":day,"is_today":day==engine.day(),"timezone":engine.config().timezone,"now":time.time(),"paused":engine.paused,"items":rows,"deleted":deleted,"adaptations":adaptations,"plan":{**plans[0],"payload":json.loads(plans[0]["payload"])} if plans else None})
        if section in ("comment_drafts","reply_drafts"):
            kind="reply" if section=="reply_drafts" else "comment"
            rows=await self.store.call("rows","SELECT id,video_id,kind,text,status,reason,root,parent,created FROM drafts WHERE kind=? ORDER BY created DESC LIMIT 20 OFFSET ?",(kind,(page-1)*20))
            total=(await self.store.call("rows","SELECT COUNT(*) AS n FROM drafts WHERE kind=?",(kind,)))[0]["n"]
            return redact({"items":rows,"page":page,"pages":max(1,(total+19)//20),"total":total})
        if section=="interaction_stats":
            rows=await self.store.call("rows","SELECT kind,state,COUNT(*) AS count FROM operations WHERE day=? GROUP BY kind,state",(engine.day(),))
            return {"items":rows,"day":engine.day()}
        tables = {
            "schedule": ("tasks", "id,type,state,day,earliest,deadline,attempts,error,payload", "created DESC"),
            "plans": ("plans", "id,day,version,active,reason,created", "created DESC"),
            "browse": ("videos", "id,bvid,page,state,reason,created,json_extract(payload,'$.title') AS title,json_extract(evidence,'$.coverage') AS coverage", "created DESC"),
            "interactions": ("drafts", "id,video_id,kind,text,status,reason,root,parent,created", "created DESC"),
            "operations": ("operations", "id,draft_id,state,video_id,kind,rpid,error,created", "created DESC"),
            "threads": ("reply_events", "id,state,created", "created DESC"),
            "memories": ("memories", "id,video_id,text,source,status,context_status,host_status,importance,updated", "updated DESC"),
            "interests": ("interests", "topic,weight,frozen", "weight DESC"),
            "changes": ("persona_changes", "id,day,topic,old,new,reason,rolled_back,created", "created DESC"),
            "logs": ("audit", "id,kind,data,created", "created DESC"),
            "models": ("model_calls", "id,stage,prompt_version,model,state,usage,created,duration", "created DESC"),
            "summaries": ("summaries", "day,text,created", "created DESC")}
        tables["persona_history"]=("persona_interventions","id,reason,rolled_back,created","created DESC")
        if section not in tables:
            raise ValueError("页面不存在")
        table, fields, order = tables[section]
        rows = await self.store.call("rows", f"SELECT {fields} FROM {table} ORDER BY {order} LIMIT 20 OFFSET ?", ((page - 1) * 20,))
        total = (await self.store.call("rows", f"SELECT COUNT(*) AS n FROM {table}"))[0]["n"]
        return redact({"items": rows, "page": page, "pages": max(1, (total + 19) // 20), "total": total})

    async def detail(self, section, record_id):
        mapping = {"schedule": ("tasks", "id"), "plans": ("plans", "id"), "browse": ("videos", "id"),
                   "interactions": ("drafts", "id"), "operations": ("operations", "id"), "threads": ("reply_events", "id"),
                   "memories": ("memories", "id"), "changes": ("persona_changes", "id"), "summaries": ("summaries", "day"),"persona_history":("persona_interventions","id")}
        if section not in mapping:
            raise ValueError("此页面不支持详情")
        table, key = mapping[section]
        rows = await self.store.call("rows", f"SELECT * FROM {table} WHERE {key}=?", (record_id,))
        if not rows:
            raise ValueError("记录不存在")
        row = rows[0]
        for key in ("payload", "evidence", "analysis", "result", "dependencies", "topics", "sources"):
            if row.get(key):
                row[key] = json.loads(row[key])
        if section == "browse":
            row["memory_status"] = await self.store.call("rows", "SELECT id,status,context_status,host_status FROM memories WHERE video_id=?", (record_id,))
        if section == "schedule":
            row["attempts_log"] = await self.store.call("rows", "SELECT * FROM attempts WHERE task_id=? ORDER BY number", (record_id,))
        return redact(row)

    async def control(self, action):
        engine = self.engine
        if action == "pause":
            engine.paused = True
            await self.store.call("set", "paused", True)
            if engine.running_job and engine.current and engine.current["type"] not in ("account", "login", "reconcile"):
                engine.running_job.cancel()
        elif action == "resume":
            engine.paused = False
            await self.store.call("set", "paused", False)
            await self.store.call("execute", "UPDATE tasks SET state='queued',next_run=? WHERE state='paused' AND cancel_requested=0 AND deadline>?", (time.time(), time.time()))
        elif action == "stop_posting":
            engine.posting_stopped = True
            await self.store.call("set", "posting_stopped", True)
        elif action == "resume_posting":
            engine.posting_stopped = False
            await self.store.call("set", "posting_stopped", False)
        elif action == "clear_restriction":
            if engine.account_status["state"] != "authenticated":
                raise ValueError("先完成登录或账号检查，再解除平台暂停")
            engine.blocked = ""
            await self.store.call("set", "blocked", "")
        elif action == "logout":
            engine.posting_stopped = True
            await self.store.call("set", "posting_stopped", True)
            if engine.current and engine.current["type"] in ("publish", "login"):
                raise ValueError("已停止新发布；请等待当前发布/扫码结束后退出账号")
            await engine.platform.logout()
            engine.account_status = {"state": "not_logged_in", "uid": "", "name": ""}
        else:
            raise ValueError("未知控制操作")
        await self.store.call("audit", "control", {"action": action})
        return {"ok": True, "action": action}

    async def model_test(self, role):
        from .model_router import ROLES
        if role not in ROLES:
            raise ValueError("未知模型角色")
        return {"ok": True, "task_id": await self.engine.enqueue("model_test", {"role": role})}

    async def save_settings(self, data):
        async with self.settings_lock:
            return await self._save_settings(data)

    async def _save_settings(self, data):
        config = Settings.model_validate_json(data, strict=True)
        if config.timezone != self.engine.config().timezone:
            rows = await self.store.call("rows", "SELECT id FROM operations LIMIT 1")
            if rows:
                raise ValueError("已有发布历史，更改时区需先停止运行并迁移额度归属")
        # 先持久化，成功后切换当前配置；发布回调会再次读取新配置。
        old=self.engine.config().model_dump()
        def persist():
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                fields=("persona_guidance","expression_preferences")
                before={k:old[k] for k in fields}
                after={k:getattr(config,k) for k in fields}
                if before!=after:
                    db.execute("INSERT INTO persona_interventions VALUES(?,?,?,?,0,?)",(uid(),dump(before),dump(after),"管理员修改兴趣与人格干预",time.time()))
                db.execute("INSERT INTO kv VALUES('settings',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(dump(config.model_dump()),))
                row=db.execute("SELECT value FROM kv WHERE key='settings_revision'").fetchone()
                revision=json.loads(row[0])+1 if row else 1
                db.execute("INSERT INTO kv VALUES('settings_revision',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(dump(revision),))
                write_settings_file(self.engine.data,config)
                return revision
        revision=await asyncio.to_thread(persist)
        self.engine.settings = config
        if self.engine.current and self.engine.running_job:
            try:
                self.engine.check_module(self.engine.current["type"])
            except RuntimeError:
                self.engine.running_job.cancel()
        await self.store.call("audit", "settings_saved", config.model_dump())
        await self.store.call("set","config_error","")
        return {"ok": True,"revision":revision}

    async def settings_patch(self, module, data):
        if module not in MODULES:
            raise ValueError("设置模块不存在")
        patch=json.loads(data)
        if not isinstance(patch,dict) or set(patch)-set(MODULES[module]["fields"]):
            raise ValueError("不能跨模块修改配置")
        async with self.settings_lock:
            return await self._save_settings(dump({**self.engine.config().model_dump(),**patch}))

    async def task_action(self, task_id, action):
        rows = await self.store.call("rows", "SELECT * FROM tasks WHERE id=?", (task_id,))
        if not rows:
            raise ValueError("任务不存在")
        row = rows[0]
        if action in ("cancel", "skip"):
            self.engine.cancelled.add(task_id)
            await self.store.call("cancel", task_id, action == "skip")
            if self.engine.current and self.engine.current["id"] == task_id and self.engine.running_job:
                self.engine.running_job.cancel()
        elif action == "resume":
            if row["state"] in ("running", "succeeded", "unknown", "cancelled", "skipped", "expired") or row["type"] == "publish":
                raise ValueError("此任务不能恢复；未知发布需核对，取消任务不可重新执行")
            if row["deadline"] <= time.time():
                raise ValueError("任务已过期，请插入新任务")
            await self.store.call("execute", "UPDATE tasks SET state='queued',next_run=?,error=NULL WHERE id=?", (time.time(), task_id))
        else:
            raise ValueError("未知任务操作")
        await self.store.call("audit", "task_control", {"task_id": task_id, "action": action})
        return {"ok": True}

    async def enqueue(self, kind, data):
        payload = json.loads(data)
        payload=self.validate_task_payload(kind,payload)
        if kind == "login":
            rows = await self.store.call("rows", "SELECT id FROM tasks WHERE type='login' AND state IN ('queued','running','retry')")
            if rows:
                return {"ok": True, "task_id": rows[0]["id"]}
        task_id = await self.engine.enqueue(kind, payload, duration=240 if kind == "login" else None)
        return {"ok": True, "task_id": task_id}

    @staticmethod
    def validate_task_payload(kind,payload):
        if not isinstance(payload, dict):
            raise ValueError("任务参数须为 JSON 对象")
        keys = {"watch": {"query"}, "discover": {"query", "max_items", "uploader", "recommended"},
                "explore": {"query", "max_items"}, "draft": {"video_id"}, "memory": {"video_id"},
                "plan": {"revise", "reason"}, "review": set(), "replies": set(), "account": set(), "login": set(), "reconcile": set(), "rest": {"reason"}}
        if kind not in keys or set(payload) - keys[kind]:
            raise ValueError("任务类型或参数不允许")
        if "max_items" in payload and (type(payload["max_items"]) is not int or not 1 <= payload["max_items"] <= 4):
            raise ValueError("浏览数量须为 1..4")
        return payload

    async def draft_save(self, draft_id, text):
        safe_public_text(text)
        count = await self.store.call("execute", "UPDATE drafts SET text=?,updated=? WHERE id=? AND status='draft'", (text, time.time(), draft_id))
        if not count:
            raise ValueError("仅未提交草稿可以编辑")
        await self.store.call("audit", "draft_edited", {"draft_id": draft_id})
        return {"ok": True}

    async def manual_draft(self, video_id, text):
        if not self.engine.config().comments_enabled:
            raise ValueError("评论模块已关闭")
        safe_public_text(text)
        rows = await self.store.call("rows", "SELECT * FROM videos WHERE id=? AND state='understood'", (video_id,))
        if not rows:
            raise ValueError("请先完成真实视频理解")
        row = rows[0]
        metadata, evidence, analysis = [json.loads(row[x]) for x in ("payload", "evidence", "analysis")]
        if not analysis.get("context_sufficient", analysis.get("suitable_for_comment")) or evidence.get("types") == ["metadata"]:
            raise ValueError("尚无足够的视频上下文证据")
        self.engine.validate_scope(metadata, analysis.get("topic", ""))
        draft_id, now = uid(), time.time()
        quote = evidence.get("segments", [{}])[0].get("text", "")[:250] if evidence.get("segments") else evidence.get("frames", [{}])[0].get("label", "")
        await self.store.call("execute", "INSERT INTO drafts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (draft_id, video_id, "comment", text, quote,
                              "管理员填写的人工草稿；非麦麦自动评论判断", "draft", self.engine.platform.uid, "", "", "", None, now, now))
        await self.store.call("audit", "manual_draft_created", {"draft_id": draft_id, "video_id": video_id})
        return {"ok": True, "draft_id": draft_id}

    async def publish(self, draft_id):
        if self.engine.posting_stopped or self.engine.paused:
            raise ValueError("暂停或停止发布状态不允许提交")
        rows=await self.store.call("rows","SELECT kind FROM drafts WHERE id=?",(draft_id,))
        if not rows or not (self.engine.config().replies_enabled if rows[0]["kind"]=="reply" else self.engine.config().comments_enabled):
            raise ValueError("草稿不存在或对应互动模块已关闭")
        return {"ok": True, "task_id": await self.engine.enqueue("publish", {"draft_id": draft_id, "manual": True})}

    async def interest_action(self, record_id, action):
        if action=="rollback_guidance":
            rows=await self.store.call("rows","SELECT * FROM persona_interventions WHERE id=? AND rolled_back=0",(record_id,))
            if not rows:
                raise ValueError("干预不存在或已回滚")
            later=await self.store.call("rows","SELECT id FROM persona_interventions WHERE created>? AND rolled_back=0",(rows[0]["created"],))
            if later:
                raise ValueError("请先回滚更新的干预版本")
            async with self.settings_lock:
                await self._save_settings(dump({**self.engine.config().model_dump(),**json.loads(rows[0]["old_value"])}))
                # 回滚自身不是新的人格版本，历史审计仍由 settings_saved 保留。
                await self.store.call("execute","UPDATE persona_interventions SET rolled_back=1 WHERE id=? OR created>?",(record_id,rows[0]["created"]))
            return {"ok":True}
        if action == "rollback":
            return await self.engine.evolution.rollback(record_id)
        if action not in ("freeze", "unfreeze"):
            raise ValueError("未知兴趣操作")
        await self.store.call("execute", "UPDATE interests SET frozen=? WHERE topic=?", (int(action == "freeze"), record_id))
        await self.store.call("audit", "interest_" + action, {"topic": record_id})
        return {"ok": True}

    async def interest_save(self, data):
        row=json.loads(data)
        if not isinstance(row,dict) or set(row)-{"topic","weight","frozen","reason"}:
            raise ValueError("兴趣参数无效")
        topic,weight,reason=row.get("topic",""),row.get("weight"),row.get("reason","")
        frozen=row.get("frozen",False)
        if not isinstance(topic,str) or not 1<=len(topic.strip())<=80 or type(weight) not in (int,float) or not 0<=weight<=1 or type(frozen) is not bool or not isinstance(reason,str) or not 1<=len(reason.strip())<=300:
            raise ValueError("需要主题、0..1权重、冻结开关和干预原因")
        topic,reason=topic.strip(),reason.strip()
        async with self.settings_lock:
            config=self.engine.config().model_copy(deep=True)
            if topic not in config.allowed_topics:
                config.allowed_topics.append(topic)
            config=Settings.model_validate(config.model_dump())
            def save():
                with self.store.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    old=db.execute("SELECT weight FROM interests WHERE topic=?",(topic,)).fetchone()
                    db.execute("INSERT INTO interests VALUES(?,?,?) ON CONFLICT(topic) DO UPDATE SET weight=excluded.weight,frozen=excluded.frozen",(topic,weight,int(frozen)))
                    change=uid()
                    db.execute("INSERT INTO persona_changes VALUES(?,?,?,?,?,?,?,?,?)",(change,self.engine.day(),topic,old[0] if old else .5,weight,"人工干预："+reason,dump(["administrator"]),0,time.time()))
                    db.execute("INSERT INTO kv VALUES('settings',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(dump(config.model_dump()),))
                    write_settings_file(self.engine.data,config)
                    return change
            change=await asyncio.to_thread(save)
            self.engine.settings=config
        await self.store.call("audit","interest_intervention",{"id":change,"topic":topic,"reason":reason})
        return {"ok":True,"change_id":change}

    async def schedule_change(self, action, data):
        args=json.loads(data)
        if not isinstance(args,dict):
            raise ValueError("日程参数必须是对象")
        reason=args.get("reason","管理员调整日程")
        if not isinstance(reason,str) or not 1<=len(reason)<=300:
            raise ValueError("调整原因需为1..300字")
        if action=="regenerate":
            if set(args)-{"reason"}:
                raise ValueError("重新生成参数无效")
            return await self.enqueue("plan",dump({"revise":True,"reason":reason}))
        if action in ("delete","restore"):
            if set(args)-{"task_id","reason"} or not isinstance(args.get("task_id"),str):
                raise ValueError("日程编号无效")
            task_id=args["task_id"]
            if action=="delete":
                row=await self.store.call("delete_task",task_id,reason)
                self.engine.cancelled.add(task_id)
                if self.engine.current and self.engine.current["id"]==task_id and self.engine.running_job:
                    self.engine.running_job.cancel()
            else:
                await self.store.call("execute","DELETE FROM task_tombstones WHERE task_id=?",(task_id,))
            await self.store.call("audit","schedule_"+action,{"task_id":task_id,"reason":reason,"note":"恢复只恢复显示，不重新执行已取消任务"})
            return {"ok":True}
        if action!="insert" or set(args)-{"kind","payload","date","start","end","duration","reason"}:
            raise ValueError("日程操作或参数无效")
        kind=args.get("kind")
        if kind not in ("watch","discover","explore","draft","replies","memory","review","rest"):
            raise ValueError("此任务不能手动安排")
        payload=args.get("payload",{})
        # 使用已有入口验证任务参数，但先检查全部时段，防止校验失败仍留下任务。
        zone=ZoneInfo(self.engine.config().timezone)
        day=args.get("date",self.engine.day())
        start=datetime.fromisoformat(day+"T"+args.get("start",self.engine.now().strftime("%H:%M"))).replace(tzinfo=zone)
        end=datetime.fromisoformat(day+"T"+args.get("end","23:59")).replace(tzinfo=zone)
        duration=args.get("duration",self.engine.config().task_timeout_seconds)
        if type(duration) is not int or not 10<=duration<=self.engine.config().task_timeout_seconds or end<=start or end.timestamp()<=time.time() or (start.date()-self.engine.now().date()).days not in range(0,8):
            raise ValueError("请选择今后七天内有效时段，执行时限不能超过任务超时")
        if kind in ("watch","discover","explore","draft","replies") and (not self.engine.active(start) or not self.engine.active(end)):
            raise ValueError("此任务时段超出活跃时间")
        # 和即时入口共用精确的参数白名单。
        validated=self.validate_task_payload(kind,payload)
        task_id=await self.engine.enqueue(kind,validated,duration=duration,earliest=start.timestamp(),deadline=end.timestamp())
        await self.store.call("execute","UPDATE tasks SET day=? WHERE id=?",(day,task_id))
        await self.store.call("execute","INSERT INTO schedule_edits VALUES(?,?,?,?,?,?,?)",(uid(),day,"insert",task_id,dump(args),reason,time.time()))
        return {"ok":True,"task_id":task_id}

    async def settings_toml(self,module=""):
        return {"text":settings_document(self.engine.config(),module),"module":module,"path":str(self.engine.data/"settings.toml"),"includes_credentials":False}

    async def settings_toml_save(self,module,data):
        patch=parse_settings_document(data,module)
        if module:
            return await self.settings_patch(module,dump(patch))
        async with self.settings_lock:
            return await self._save_settings(dump({**self.engine.config().model_dump(),**patch}))

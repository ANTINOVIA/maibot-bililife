from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import json
import asyncio
import time

from .maibot_adapter import ModelUnavailable
from .schemas import PlanProposal, ScheduleAdjustment
from .storage import dump, uid


class DailyPlanner:
    def __init__(self, engine):
        self.engine, self.store, self.host = engine, engine.store, engine.host

    async def create(self, revise=False, reason="每日主计划"):
        day, cfg, now = self.engine.day(), self.engine.config(), self.engine.now()
        old = await self.store.call("rows", "SELECT id FROM plans WHERE day=? AND active=1", (day,))
        if old and not revise:
            return {"plan_id": old[0]["id"], "existing": True}
        interests = await self.store.call("rows", "SELECT * FROM interests")
        summaries = await self.store.call("rows", "SELECT day,payload,text FROM summaries ORDER BY day DESC LIMIT 2")
        recent = await self.store.call("rows", "SELECT bvid,state,reason,analysis FROM videos ORDER BY created DESC LIMIT 8")
        # 计划需要复盘结论和未解决问题，不携带复盘中完整执行流水及旧模型长回答。
        summaries=[{"day":row["day"],"summary":row["text"][:2000],
                    **{key:json.loads(row["payload"]).get("review",{}).get(key,[])[:6] for key in ("learned","unresolved","tomorrow")}} for row in summaries]
        recent=[{**{key:row[key] for key in ("bvid","state","reason")},
                 "analysis":{key:json.loads(row["analysis"]).get(key,"") for key in ("summary","feeling","questions","topic")}} for row in recent]
        pending = await self.store.call("rows", "SELECT type,state,payload,error FROM tasks WHERE day<? AND state NOT IN ('succeeded','cancelled','skipped') ORDER BY created DESC LIMIT 8", (day,))
        interactions = await self.store.call("rows", "SELECT kind,state,video_id,text FROM operations ORDER BY created DESC LIMIT 8")
        budget = await self.store.call("rows", "SELECT * FROM budget WHERE day=?", (day,))
        manual=await self.store.call("rows","SELECT type,earliest,deadline,payload FROM tasks WHERE day=? AND plan_id IS NULL AND state IN ('queued','retry','paused') AND type IN ('watch','discover','explore','read_comments','rest','memory','review','draft','replies') ORDER BY earliest LIMIT 10",(day,))
        external = {"date": day, "now": now.isoformat(), "timezone": cfg.timezone,
                    "settings": cfg.model_dump(exclude={"prompt_overrides","owner_uid"}), "interests": interests,
                    "recent": recent, "summaries": summaries, "unfinished": pending, "interactions": interactions,
                    "manual_schedule":manual,"budget_usage": budget, "account": self.engine.account_status, "health": self.engine.health}
        external['recent_comment_reading']=await self.store.call('rows',"SELECT bvid,state,json_extract(analysis,'$.summary') AS summary FROM comment_reads ORDER BY created DESC LIMIT 4")
        degraded = ""
        proposal = None
        try:
            proposal = await self.host.generate("plan", external, PlanProposal)
            tasks = self.validate(proposal, now)
        except (ValueError, ModelUnavailable) as exc:
            degraded = type(exc).__name__ + "：计划生成失败，保守降级为休息与复盘"
            tasks = [{"id": uid(), "type": "rest", "earliest": time.time(), "deadline": self.engine.day_end(), "duration": 30,
                      "dependencies": [], "payload": {"goal": "休息", "reason": degraded}}]
            proposal = PlanProposal(theme="保守休息：待模型/计划恢复", tasks=[{
                "type": "rest", "earliest_start": now.isoformat(), "deadline": datetime.fromtimestamp(self.engine.day_end(), ZoneInfo(cfg.timezone)).isoformat(),
                "goal": "休息并保留真实错误状态", "reason": degraded}])
            await self.store.call("set", "plan_error", degraded)
        else:
            await self.store.call("set", "plan_error", "")
        payload = proposal.model_dump()
        payload.update(timezone=cfg.timezone, degraded=bool(degraded), error=degraded)
        for index, task in enumerate(tasks):
            if index < len(payload["tasks"]):
                payload["tasks"][index]["task_id"] = task["id"]
        plan_id = await self.store.call("save_plan", day, payload, tasks, reason, revise)
        await self.store.call("audit", "plan_created", {"plan_id": plan_id, "reason": reason, "degraded": degraded})
        return {"plan_id": plan_id, "degraded": bool(degraded), "reason": degraded}

    async def adapt(self, video_id):
        cfg=self.engine.config()
        if not cfg.adaptive_schedule_enabled or not cfg.max_dynamic_tasks:
            return {"inserted":[],"reason":"自主追加日程已关闭"}
        day,key=self.engine.day(),self.engine.day()+":"+video_id
        claimed=await self.store.call("execute","INSERT OR IGNORE INTO adaptive_decisions VALUES(?,?,?,'requesting','{}',?)",(key,day,video_id,time.time()))
        if not claimed:
            return {"inserted":[],"reason":"此经历已评估，不重复追加"}
        try:
            count=(await self.store.call("rows","SELECT COUNT(*) AS n FROM schedule_edits WHERE day=? AND action='agent_insert'",(day,)))[0]["n"]
            if count>=cfg.max_dynamic_tasks:
                return {"inserted":[],"reason":"当日自主追加额度已用完"}
            rows=await self.store.call("rows","SELECT payload,analysis FROM videos WHERE id=? AND state='understood'",(video_id,))
            if not rows:
                raise ValueError("只能根据已经理解的经历追加日程")
            context={"video":json.loads(rows[0]["payload"]),"understanding":json.loads(rows[0]["analysis"]),
                     "settings":cfg.model_dump(exclude={"prompt_overrides","owner_uid"}),"now":self.engine.now().isoformat(),
                     "remaining_insertions":cfg.max_dynamic_tasks-count,
                     "budget":await self.store.call("rows","SELECT resource,used FROM budget WHERE day=?",(day,)),
                     "schedule":await self.store.call("rows","SELECT type,payload,earliest,deadline FROM tasks WHERE day=? AND state IN ('queued','running','retry') LIMIT 15",(day,))}
            proposal=await self.host.generate("adapt",context,ScheduleAdjustment)
            if len(proposal.tasks)>cfg.max_dynamic_tasks-count:
                raise ValueError("追加任务超过剩余额度")
            tasks=self.validate(proposal,self.engine.now())
            self.engine.check()
            # 配置可能在模型等待期间改变，事务前再核对。
            if not self.engine.config().adaptive_schedule_enabled:
                raise ValueError("自主追加已关闭")
            def insert():
                with self.store.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    used=db.execute("SELECT COUNT(*) FROM schedule_edits WHERE day=? AND action='agent_insert'",(day,)).fetchone()[0]
                    if used+len(tasks)>self.engine.config().max_dynamic_tasks:
                        raise ValueError("自主追加额度不足")
                    budget={r['resource']:r['used'] for r in db.execute("SELECT resource,used FROM budget WHERE day=?",(day,))}
                    pending=db.execute("SELECT type,payload FROM tasks WHERE day=? AND state IN ('queued','retry') AND type IN ('discover','explore','watch')",(day,)).fetchall()
                    reserved=sum(1 if r['type']=='watch' else json.loads(r['payload']).get('max_items',1) for r in pending)
                    added=sum(t['payload']['max_items'] for t in tasks if t['type'] in ('discover','explore'))
                    if added and budget.get('videos',0)+reserved+added>cfg.max_videos:
                        raise ValueError("剩余浏览额度不足，已有日程优先")
                    comments_reserved=db.execute("SELECT COUNT(*) FROM tasks WHERE day=? AND type='read_comments' AND state IN ('queued','retry')",(day,)).fetchone()[0]
                    comment_added=sum(task['type']=='read_comments' for task in tasks)
                    if comment_added and budget.get('comment_sessions',0)+comments_reserved+comment_added>cfg.max_comment_sessions:
                        raise ValueError('剩余只读评论额度不足，已有日程优先')
                    for task in tasks:
                        db.execute("INSERT INTO tasks(id,day,type,state,payload,earliest,deadline,next_run,duration,dependencies,created,updated) VALUES(?,?,?,'queued',?,?,?,?,?,?,?,?)",
                                   (task['id'],day,task['type'],dump({**task['payload'],'origin':'agent','source_video_id':video_id}),task['earliest'],task['deadline'],task['earliest'],task['duration'],dump(task['dependencies']),time.time(),time.time()))
                        db.execute("INSERT INTO schedule_edits VALUES(?,?,?,?,?,?,?)",(uid(),day,'agent_insert',task['id'],dump({'video_id':video_id}),proposal.reason,time.time()))
                    result={'inserted':[t['id'] for t in tasks],'reason':proposal.reason}
                    db.execute("UPDATE adaptive_decisions SET state='completed',payload=? WHERE source_key=?",(dump(result),key))
                    return result
            return await asyncio.to_thread(insert)
        except BaseException as exc:
            await asyncio.shield(self.store.call("execute","UPDATE adaptive_decisions SET state='failed',payload=? WHERE source_key=? AND state='requesting'",(dump({"inserted":[],"reason":type(exc).__name__+"：自主追加未执行"}),key)))
            raise
        finally:
            await self.store.call("execute","UPDATE adaptive_decisions SET state='completed',payload=? WHERE source_key=? AND state='requesting'",(dump({"inserted":[],"reason":"自主追加额度已用完"}),key))

    def validate(self, proposal, now):
        cfg, tasks, ids = self.engine.config(), [], [uid() for _ in proposal.tasks]
        total_items, total_duration, comment_sessions = 0, 0, 0
        for index, item in enumerate(proposal.tasks):
            try:
                self.engine.check_module(item.type)
            except RuntimeError:
                raise ValueError("计划包含已关闭的模块") from None
            start, end = datetime.fromisoformat(item.earliest_start), datetime.fromisoformat(item.deadline)
            start, end = start.astimezone(ZoneInfo(cfg.timezone)), end.astimezone(ZoneInfo(cfg.timezone))
            if start.date() != now.date() or end.date() != now.date() or end.timestamp() <= time.time():
                raise ValueError("任务超出当日或已经过期")
            if any(type(x) is not int or x < 0 or x >= index for x in item.depends_on):
                raise ValueError("任务依赖必须是前序任务索引，禁止循环")
            if item.type not in ("rest", "memory", "review"):
                if not self.engine.active(start) or not self.engine.active(end):
                    raise ValueError("任务超出允许活跃时段")
                if item.type in ("discover", "explore", "read_comments"):
                    if item.query:
                        try:
                            self.engine.validate_query(item.query)
                        except RuntimeError:
                            raise ValueError("计划检索词超出允许主题") from None
                    if item.type=='read_comments':
                        comment_sessions+=1
                    else:
                        total_items += item.max_items
                if item.type == "watch":
                    from .bilibili_adapter import parse_video
                    parse_video(item.query)
                    total_items += 1
                if item.type!='read_comments':total_duration += item.max_duration_seconds
            tasks.append({"id": ids[index], "type": item.type, "earliest": max(start.timestamp(), time.time()), "deadline": end.timestamp(),
                          "duration": min(item.max_duration_seconds, cfg.task_timeout_seconds),
                          "dependencies": [ids[x] for x in item.depends_on], "payload": item.model_dump()})
        if total_items > cfg.max_videos or total_duration > cfg.max_browse_seconds or comment_sessions>cfg.max_comment_sessions:
            raise ValueError("计划超过每日数量或时间预算")
        return tasks

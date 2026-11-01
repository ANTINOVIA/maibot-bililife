from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import json
import asyncio
import time

import aiohttp
import httpx

from .maibot_adapter import ModelUnavailable
from .schemas import PlanProposal, ScheduleAdjustment
from .storage import BudgetExceeded, dump, uid
from .security import redact
from .bilibili_adapter import parse_video


class PlanValidationError(ValueError):
    """A schema-valid proposal violates a repairable plan rule."""


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
        manual=await self.store.call("rows","SELECT type,earliest,deadline,payload FROM tasks WHERE day=? AND plan_id IS NULL AND state IN ('queued','retry','paused') AND type IN ('watch','discover','explore','read_comments','rest','memory','review','draft','replies','publish') ORDER BY earliest LIMIT 10",(day,))
        external = {"date": day, "now": now.isoformat(), "timezone": cfg.timezone,
                    "current_rest_until": self.engine.rest_until(),
                    "settings": cfg.model_dump(include={'allowed_topics','excluded_topics','allowed_uploaders','browsing_enabled',
                        'comments_enabled','replies_enabled','memory_enabled','review_enabled','max_videos',
                        'max_browse_seconds','max_comment_sessions','active_start','active_end'}), "interests": interests,
                    "recent": recent, "summaries": summaries, "unfinished": pending, "interactions": interactions,
                    "manual_schedule":manual,"budget_usage": budget, "account": self.engine.account_status, "health": self.engine.health}
        external['recent_comment_reading']=await self.store.call('rows',"SELECT bvid,state,json_extract(analysis,'$.summary') AS summary FROM comment_reads ORDER BY created DESC LIMIT 4")
        try:
            proposal = await self.host.generate("plan", external, PlanProposal)
            try:
                tasks = self.validate(proposal, now, replacing=True)
            except PlanValidationError as exc:
                # One semantic repair only; the host independently bounds JSON repair
                # and accounts every model call against the existing quotas.
                current_settings = self.engine.config().model_dump(include=set(external["settings"]))
                repair = {"date": day, "now": now.isoformat(), "timezone": cfg.timezone,
                          "settings": current_settings, "budget_usage": budget,
                          "repair": {"error": redact(str(exc)).replace("\n", " ")[:160],
                                     "previous_proposal": redact(proposal.model_dump(exclude_defaults=True))}}
                proposal = await self.host.generate("plan", repair, PlanProposal)
                tasks = self.validate(proposal, self.engine.now(), replacing=True)
            if not tasks:
                raise ValueError("没有活动能放入剩余时段或预算")
            payload = proposal.model_dump()
            payload.update(timezone=cfg.timezone, tasks=[t['payload'] for t in tasks], omitted=self.omitted)
            for item, task in zip(payload["tasks"], tasks):
                item.update(task_id=task["id"])
            plan_id = await self.store.call("save_plan", day, payload, tasks, reason, revise)
        except (ValueError, ModelUnavailable, BudgetExceeded, httpx.TransportError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            detail = redact(str(exc)).replace("\n", " ")[:160]
            message = type(exc).__name__ + "：" + (detail or "计划生成失败，请检查模型配置与额度")
            await self.store.call("set", "plan_error", message)
            raise
        await self.store.call("set", "plan_error", "")
        await self.store.call("set", "plan_retry", {})
        await self.store.call("audit", "plan_created", {"plan_id": plan_id, "reason": reason})
        return {"plan_id": plan_id, "omitted": self.omitted}

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
                     "current_rest_until":self.engine.rest_until(),
                     "settings":cfg.model_dump(exclude={"prompt_overrides","owner_uid"}),"now":self.engine.now().isoformat(),
                     "remaining_insertions":cfg.max_dynamic_tasks-count,
                     "budget":await self.store.call("rows","SELECT resource,used FROM budget WHERE day=?",(day,)),
                     "schedule":await self.store.call("rows","SELECT type,payload,earliest,deadline FROM tasks WHERE day=? AND state IN ('queued','running','retry') LIMIT 15",(day,))}
            proposal=await self.host.generate("adapt",context,ScheduleAdjustment)
            if len(proposal.tasks)>cfg.max_dynamic_tasks-count:
                raise ValueError("追加任务超过剩余额度")
            tasks=self.validate(proposal,self.engine.now())
            omitted = list(self.omitted)
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
                    reserved=0
                    for row in pending:
                        payload=json.loads(row['payload'])
                        if row['type']!='watch':
                            reserved+=payload.get('max_items',1)
                            continue
                        try:
                            bvid,page=parse_video(payload.get('query',payload.get('video','')))
                        except ValueError:
                            reserved+=1
                            continue
                        if not db.execute('SELECT 1 FROM budget_reservations WHERE day=? AND reservation_key=?',
                                          (day,f'watch:{bvid}:p{page}')).fetchone():
                            reserved+=1
                    added=sum(t['payload']['max_items'] for t in tasks if t['type'] in ('discover','explore'))
                    if added and budget.get('videos',0)+reserved+added>cfg.max_videos:
                        raise ValueError("剩余浏览额度不足，已有日程优先")
                    comments_reserved=db.execute("SELECT COUNT(*) FROM tasks WHERE day=? AND type='read_comments' AND state IN ('queued','retry')",(day,)).fetchone()[0]
                    comment_added=sum(task['type']=='read_comments' for task in tasks)
                    if comment_added and budget.get('comment_sessions',0)+comments_reserved+comment_added>cfg.max_comment_sessions:
                        raise ValueError('剩余只读评论额度不足，已有日程优先')
                    for task in tasks:
                        if self.store.slot_conflicts(db, day, task['earliest'], task['deadline']):
                            raise ValueError('日程已变化，活动时段发生冲突，请重试')
                        db.execute("INSERT INTO tasks(id,day,type,state,payload,earliest,deadline,next_run,duration,dependencies,created,updated) VALUES(?,?,?,'queued',?,?,?,?,?,?,?,?)",
                                   (task['id'],day,task['type'],dump({**task['payload'],'origin':'agent','source_video_id':video_id}),task['earliest'],task['deadline'],task['earliest'],task['duration'],dump(task['dependencies']),time.time(),time.time()))
                        db.execute("INSERT INTO schedule_edits VALUES(?,?,?,?,?,?,?)",(uid(),day,'agent_insert',task['id'],dump({'video_id':video_id}),proposal.reason,time.time()))
                    result={'inserted':[t['id'] for t in tasks],'reason':proposal.reason,'omitted':omitted}
                    db.execute("UPDATE adaptive_decisions SET state='completed',payload=? WHERE source_key=?",(dump(result),key))
                    return result
            return await asyncio.to_thread(insert)
        except BaseException as exc:
            await asyncio.shield(self.store.call("execute","UPDATE adaptive_decisions SET state='failed',payload=? WHERE source_key=? AND state='requesting'",(dump({"inserted":[],"reason":type(exc).__name__+"：自主追加未执行"}),key)))
            raise
        finally:
            await self.store.call("execute","UPDATE adaptive_decisions SET state='completed',payload=? WHERE source_key=? AND state='requesting'",(dump({"inserted":[],"reason":"自主追加额度已用完"}),key))

    def validate(self, proposal, now, replacing=False):
        cfg, tasks, ids = self.engine.config(), [], [uid() for _ in proposal.tasks]
        self.omitted = []
        occupied = self.store.rows("""SELECT t.earliest,t.deadline FROM tasks t
            LEFT JOIN task_tombstones d ON d.task_id=t.id LEFT JOIN plans p ON p.id=t.plan_id
            WHERE t.day=? AND d.task_id IS NULL AND t.deadline>?
              AND t.type IN ('watch','discover','explore','read_comments','rest','memory','review','draft','replies','publish')
              AND t.state IN ('queued','retry','paused','running','succeeded')
              AND NOT (COALESCE(json_extract(p.payload,'$.degraded'),0)=1 AND COALESCE(json_extract(p.payload,'$.theme'),'') LIKE '保守休息：待模型/计划恢复%')
              AND (?=0 OR t.plan_id IS NULL OR t.state IN ('running','succeeded') OR json_extract(t.payload,'$._slot_owner') IS NOT NULL OR EXISTS (SELECT 1 FROM tasks c WHERE json_extract(c.payload,'$._slot_owner')=t.id AND c.state IN ('queued','retry','paused','running'))) ORDER BY t.earliest""",
            (now.date().isoformat(), now.timestamp(), int(replacing)))
        intervals = [(r['earliest'], r['deadline']) for r in occupied]
        used = {r['resource']: r['used'] for r in self.store.rows("SELECT resource,used FROM budget WHERE day=?", (now.date().isoformat(),))}
        total_items = used.get('videos', 0)
        total_duration = used.get('browse_seconds', 0)
        comment_sessions = used.get('comment_sessions', 0)
        # Pending independent activities reserve daily quantities as well as time.
        pending = self.store.rows("""SELECT type,payload FROM tasks WHERE day=? AND state IN ('queued','retry','paused','running')
            AND type IN ('discover','explore','watch','read_comments')
            AND (?=0 OR plan_id IS NULL OR state='running' OR json_extract(payload,'$._slot_owner') IS NOT NULL)""",
            (now.date().isoformat(), int(replacing)))
        for row in pending:
            data = json.loads(row['payload'])
            if row['type'] == 'read_comments':
                comment_sessions += 1
            elif row['type'] in ('discover','explore'):
                total_items += data.get('max_items', 1)
            elif row['type'] == 'watch':
                try:
                    bvid, page = parse_video(data.get('query', data.get('video','')))
                    reserved = self.store.rows('SELECT 1 FROM budget_reservations WHERE day=? AND reservation_key=?',
                                               (now.date().isoformat(), f'watch:{bvid}:p{page}'))
                    total_items += 0 if reserved else 1
                except ValueError:
                    total_items += 1
        previous_end = None
        previous_type = None
        accepted = set()
        for index, item in enumerate(proposal.tasks):
            try:
                self.engine.check_module(item.type)
            except RuntimeError:
                raise PlanValidationError("计划包含已关闭的模块") from None
            if any(type(x) is not int or x < 0 or x >= index for x in item.depends_on):
                raise PlanValidationError("任务依赖必须是前序任务索引，禁止循环")
            if any(x not in accepted for x in item.depends_on):
                self.omitted.append({'index': index, 'reason': '依赖活动未能安排'})
                continue
            if item.type in ('discover', 'explore', 'read_comments') and item.query:
                try:
                    self.engine.validate_query(item.query)
                except RuntimeError:
                    raise PlanValidationError("计划检索词须逐字包含允许主题且不能包含排除主题") from None
            if item.type == 'watch':
                parse_video(item.query)
            start = datetime.fromisoformat(item.earliest_start).astimezone(ZoneInfo(cfg.timezone)) if item.earliest_start else now
            end = datetime.fromisoformat(item.deadline).astimezone(ZoneInfo(cfg.timezone)) if item.deadline else now.replace(hour=23, minute=59, second=59, microsecond=0)
            if start.date() != now.date() or end.date() != now.date():
                raise ValueError("任务时间须在当天")
            requested = (end-start).total_seconds() if item.earliest_start and item.deadline else None
            window = cfg.schedule_window_seconds(item.type, requested)
            earliest = max(start.timestamp(), now.timestamp(), time.time())
            if previous_end is not None:
                earliest = max(earliest, previous_end + (0 if item.type == 'rest' or previous_type == 'rest' else cfg.schedule_gap_minutes*60))
            day_end = self.engine.day_end()
            if item.type not in ('rest', 'memory', 'review'):
                hour, minute = map(int, cfg.active_start.split(':'))
                active_start = now.replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp()
                hour, minute = map(int, cfg.active_end.split(':'))
                active_end = now.replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp()
                midnight = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
                windows = [(active_start, active_end)] if active_start <= active_end else [(midnight, active_end), (active_start, day_end)]
            else:
                windows = [(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp(), day_end)]
            # Legacy timestamps specify preferences, not a fixed calendar reservation.
            placed = None
            for window_start, window_end in windows:
                candidate = max(earliest, window_start)
                for lo, hi in sorted(intervals):
                    if candidate < hi and candidate + window > lo:
                        candidate = hi
                if candidate + window <= window_end:
                    placed = candidate
                    break
            add_items = item.max_items if item.type in ('discover','explore') else 1 if item.type == 'watch' else 0
            add_duration = item.max_duration_seconds if item.type not in ('rest','memory','review','read_comments') else 0
            add_comments = int(item.type == 'read_comments')
            if placed is None:
                self.omitted.append({'index': index, 'reason': '剩余时段不足'})
                continue
            earliest = placed
            if total_items + add_items > cfg.max_videos or total_duration + add_duration > cfg.max_browse_seconds or comment_sessions + add_comments > cfg.max_comment_sessions:
                self.omitted.append({'index': index, 'reason': '剩余预算不足'})
                continue
            total_items += add_items; total_duration += add_duration; comment_sessions += add_comments
            start = datetime.fromtimestamp(earliest, ZoneInfo(cfg.timezone))
            end = datetime.fromtimestamp(earliest + window, ZoneInfo(cfg.timezone))
            duration = min(item.max_duration_seconds, cfg.task_timeout_seconds, int(window))
            task_payload = {**item.model_dump(), 'earliest_start': start.isoformat(), 'deadline': end.isoformat(), 'max_duration_seconds': duration}
            tasks.append({'id': ids[index], 'type': item.type, 'earliest': earliest, 'deadline': earliest+window,
                          'duration': duration, 'dependencies': [ids[x] for x in item.depends_on], 'payload': task_payload})
            accepted.add(index); intervals.append((earliest, earliest+window))
            previous_end = earliest+window; previous_type = item.type
        return tasks

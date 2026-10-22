from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import json
import time

from .maibot_adapter import ModelUnavailable
from .schemas import PlanProposal
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
        manual=await self.store.call("rows","SELECT type,earliest,deadline,payload FROM tasks WHERE day=? AND plan_id IS NULL AND state IN ('queued','retry','paused') AND type IN ('watch','discover','explore','rest','memory','review','draft','replies') ORDER BY earliest LIMIT 10",(day,))
        external = {"date": day, "now": now.isoformat(), "timezone": cfg.timezone,
                    "settings": cfg.model_dump(exclude={"prompt_overrides"}), "interests": interests,
                    "recent": recent, "summaries": summaries, "unfinished": pending, "interactions": interactions,
                    "manual_schedule":manual,"budget_usage": budget, "account": self.engine.account_status, "health": self.engine.health}
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

    def validate(self, proposal, now):
        cfg, tasks, ids = self.engine.config(), [], [uid() for _ in proposal.tasks]
        total_items, total_duration = 0, 0
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
                if item.type in ("discover", "explore"):
                    try:
                        self.engine.validate_query(item.query)
                    except RuntimeError:
                        raise ValueError("计划检索词超出允许主题") from None
                    total_items += item.max_items
                if item.type == "watch":
                    from .bilibili_adapter import parse_video
                    parse_video(item.query)
                    total_items += 1
                total_duration += item.max_duration_seconds
            tasks.append({"id": ids[index], "type": item.type, "earliest": max(start.timestamp(), time.time()), "deadline": end.timestamp(),
                          "duration": min(item.max_duration_seconds, cfg.task_timeout_seconds),
                          "dependencies": [ids[x] for x in item.depends_on], "payload": item.model_dump()})
        if total_items > cfg.max_videos or total_duration > cfg.max_browse_seconds:
            raise ValueError("计划超过每日数量或时间预算")
        return tasks

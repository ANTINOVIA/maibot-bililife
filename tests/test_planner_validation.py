from datetime import timedelta

import pytest

from bililife.schemas import PlanProposal


async def test_plan_success_clears_old_error_and_same_day_reuses_plan(engine):
    now=engine.now()
    proposal=PlanProposal(theme='休息',tasks=[{'type':'rest','earliest_start':now.isoformat(),
        'deadline':(now+timedelta(seconds=60)).isoformat(),'goal':'休息','reason':'测试时间窗口'}])
    async def generate(*args):
        return proposal
    engine.host.generate=generate
    engine.store.set('plan_error','previous failure')
    first=await engine.planner.create()
    second=await engine.planner.create()
    assert first['degraded'] is False and second['existing'] is True
    assert first['plan_id']==second['plan_id']
    assert engine.store.get('plan_error')==''
    await engine.platform.close()


async def test_planner_uses_summary_conclusions_without_nested_execution_log(engine):
    import json
    import time
    engine.store.execute('INSERT INTO summaries VALUES(?,?,?,?)',(engine.day(),json.dumps({'review':{'unresolved':['问题来源待核对']},'records':{'giant_audit':'x'*50000}}),'真实执行的摘要',time.time()))
    now=engine.now()
    async def generate(stage,data,schema):
        assert 'giant_audit' not in json.dumps(data)
        assert data['summaries'][0]['unresolved']==['问题来源待核对']
        return PlanProposal(theme='休息',tasks=[{'type':'rest','earliest_start':now.isoformat(),'deadline':(now+timedelta(seconds=60)).isoformat(),'goal':'休息','reason':'资料不足'}])
    engine.host.generate=generate
    assert (await engine.planner.create())['degraded'] is False
    await engine.platform.close()


async def test_invalid_query_degrades_instead_of_executing(engine):
    now=engine.now()
    engine.active=lambda *args:True
    proposal=PlanProposal(theme='无授权主题',tasks=[{'type':'discover','earliest_start':now.isoformat(),
        'deadline':(now+timedelta(seconds=60)).isoformat(),'goal':'搜索','query':'无授权主题','reason':'invalid'}])
    async def generate(*args):
        return proposal
    engine.host.generate=generate
    result=await engine.planner.create()
    assert result['degraded'] is True
    assert engine.store.rows('SELECT type FROM tasks')==[{'type':'rest'}]
    await engine.platform.close()

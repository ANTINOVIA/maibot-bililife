import httpx
import pytest

from bililife.configuration import Settings
from bililife.daily_planner import PlanValidationError
from bililife.schemas import PlanProposal
from bililife.storage import BudgetExceeded
from test_slot_regressions import fixed


def plan(query):
    return PlanProposal(theme='探索', tasks=[{'type':'discover','query':query,'goal':'探索','reason':'好奇'}])


async def test_plan_invalid_query_gets_one_repair_with_exact_permissions(engine, monkeypatch):
    fixed(engine, monkeypatch)
    engine.settings = Settings(active_start='00:00',active_end='23:59',allowed_topics=['科学实验'],excluded_topics=['禁词'])
    calls=[]
    async def generate(stage, context, schema):
        calls.append(context)
        if len(calls)==1: return plan('科学演示')
        assert context['settings']['allowed_topics']==['科学实验']
        assert context['settings']['excluded_topics']==['禁词']
        assert context['repair']['previous_proposal']['tasks'][0]['query']=='科学演示'
        assert '逐字' in context['repair']['error']
        return plan('科学实验 演示')
    engine.host.generate=generate
    result=await engine.planner.create()
    assert result['plan_id'] and len(calls)==2
    assert engine.store.rows('SELECT id FROM plans WHERE active=1')
    await engine.platform.close()


async def test_twice_invalid_preserves_old_plan_without_fallback(engine, monkeypatch):
    fixed(engine, monkeypatch)
    calls=[]
    async def good(*args): return plan('科学')
    engine.host.generate=good
    old=(await engine.planner.create())['plan_id']
    async def invalid(*args):
        calls.append(1)
        return plan('不匹配')
    engine.host.generate=invalid
    with pytest.raises(PlanValidationError): await engine.planner.create(revise=True)
    assert len(calls)==2
    assert engine.store.rows('SELECT id FROM plans WHERE active=1')==[{'id':old}]
    assert len(engine.store.rows('SELECT id FROM plans'))==1
    assert '逐字' in engine.store.get('plan_error')
    await engine.platform.close()


@pytest.mark.parametrize('exc', [httpx.ConnectError('network'),BudgetExceeded('额度耗尽')])
async def test_transport_or_budget_does_not_trigger_semantic_repair(engine, monkeypatch, exc):
    fixed(engine, monkeypatch)
    calls=[]
    async def fail(*args):
        calls.append(1)
        raise exc
    engine.host.generate=fail
    with pytest.raises(type(exc)): await engine.planner.create()
    assert len(calls)==1 and not engine.store.rows('SELECT id FROM plans')
    await engine.platform.close()


async def test_unfit_plan_does_not_trigger_semantic_repair(engine, monkeypatch):
    fixed(engine, monkeypatch,23)
    engine.settings=Settings(active_start='00:00',active_end='22:00')
    calls=[]
    async def generate(*args):
        calls.append(1)
        return plan('科学')
    engine.host.generate=generate
    with pytest.raises(ValueError, match='剩余时段'): await engine.planner.create()
    assert len(calls)==1 and not engine.store.rows('SELECT id FROM plans')
    await engine.platform.close()

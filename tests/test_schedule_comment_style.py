from datetime import timedelta
from types import SimpleNamespace
import json
import time

import pytest

from bililife.configuration import Settings, DEFAULT_COMMENT_PROMPT
from bililife.config_io import parse_settings_document, settings_document
from bililife.interaction_service import PolicyBlocked
from bililife.schemas import Draft, InteractionDecision, PlanProposal
from bililife.webui_service import WebUIService
from test_invariants import seed_video


def fixed_morning(engine, monkeypatch):
    now = engine.now().replace(hour=10, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(time, 'time', lambda: now.timestamp())
    engine.now = lambda: now
    return now


async def test_generated_plan_windows_and_saved_payload_agree(engine, monkeypatch):
    now = fixed_morning(engine, monkeypatch)
    proposal = PlanProposal(theme='看看科学', tasks=[{
        'type': kind, 'earliest_start': now.isoformat(),
        'deadline': now.replace(hour=22).isoformat(), 'goal': '有限安排', 'query': '科学', 'reason': '感兴趣'
    } for kind in ('discover', 'rest', 'read_comments')])
    async def generated(*args): return proposal
    engine.host.generate = generated
    await engine.planner.create()
    tasks = engine.store.rows('SELECT * FROM tasks ORDER BY earliest')
    plan = json.loads(engine.store.rows('SELECT payload FROM plans')[0]['payload'])
    assert len(tasks) == 3
    for index, row in enumerate(tasks):
        assert row['deadline'] - row['earliest'] == [1800,10800,1800][index]
        assert plan['tasks'][index]['deadline'] == json.loads(row['payload'])['deadline']
        if index: assert row['earliest'] == tasks[index-1]['deadline']
    await engine.platform.close()


async def test_fallback_and_default_manual_window_are_short(engine, monkeypatch):
    now = fixed_morning(engine, monkeypatch)
    async def invalid(*args): raise ValueError('模型日程无效')
    engine.host.generate = invalid
    assert (await engine.planner.create())['degraded']
    first = engine.store.rows('SELECT * FROM tasks')[0]
    assert first['deadline'] - first['earliest'] == 3600
    tomorrow = (now + timedelta(days=1)).date().isoformat()
    result = await WebUIService(engine).schedule_change('insert', json.dumps({
        'kind':'rest','date':tomorrow,'start':'11:00','reason':'稍作休息'}))
    row = engine.store.rows('SELECT * FROM tasks WHERE id=?', (result['task_id'],))[0]
    assert row['deadline'] - row['earliest'] == 3600
    await engine.platform.close()


async def test_configurable_windows_children_and_toml_persist(engine, monkeypatch):
    now = fixed_morning(engine, monkeypatch)
    web = WebUIService(engine)
    await web.settings_patch('schedule', json.dumps({'schedule_task_minutes':5,'schedule_brief_minutes':5,'schedule_gap_minutes':2}))
    assert parse_settings_document(settings_document(engine.config(), 'schedule'), 'schedule')['schedule_task_minutes'] == 5
    task_id = await engine.enqueue('memory', {})
    row = engine.store.rows('SELECT * FROM tasks WHERE id=?', (task_id,))[0]
    assert row['deadline'] - row['earliest'] == 300
    assert engine.life_deadline() - now.timestamp() == 300
    await web.settings_patch('comments', json.dumps({'comment_prompt':'简短说具体看法','comment_avoid_phrases':['我记下了']}))
    assert parse_settings_document(settings_document(engine.config(), 'comments'), 'comments')['comment_prompt'] == '简短说具体看法'
    for bad in ({'schedule_task_minutes':2}, {'schedule_gap_minutes':181}, {'comment_prompt':'a'*3001}, {'comment_avoid_phrases':['']}):
        with pytest.raises(ValueError): Settings(**bad)
    await engine.platform.close()


@pytest.mark.parametrize('stage', ['comment', 'reply'])
async def test_comment_prompt_reaches_real_model_adapter(engine, stage):
    async def profile(*args): return {'personality':'宿主人格'}
    engine.host.ctx = SimpleNamespace(config=SimpleNamespace(get=profile))
    captured = []
    async def generate(messages, **kwargs):
        captured.append(messages[0]['content'])
        return {'success':True,'model':'test','response':Draft(text='这个细节挺有意思', evidence_quote='原文', reason='具体').model_dump_json()}
    engine.host.router.generate = generate
    await engine.host.generate(stage, {}, Draft)
    assert DEFAULT_COMMENT_PROMPT in captured[0] and '我记下了' in captured[0]
    await engine.platform.close()


@pytest.mark.parametrize('always_bad', [False, True])
async def test_comment_style_has_bounded_repair(engine, always_bad):
    seed_video(engine)
    engine.store.execute('DELETE FROM drafts')
    seen = []
    async def context(*args): return []
    async def generate(stage, external, schema, *args):
        if schema is InteractionDecision:
            return InteractionDecision(interact=True, reason='想讨论细节', evidence_quote='水在加热后发生变化')
        seen.append(external.get('style_feedback'))
        return Draft(text='我，记 下了这个变化' if always_bad or len(seen)==1 else '加热后这个变化出现得挺快',
                     evidence_quote='水在加热后发生变化', reason='回应具体变化')
    engine.platform.comment_context = context
    engine.host.generate = generate
    if always_bad:
        with pytest.raises(PolicyBlocked, match='两次'): await engine.interaction.create_draft('v1')
        assert not engine.store.rows('SELECT * FROM drafts')
    else:
        assert (await engine.interaction.create_draft('v1'))['drafted']
        assert '加热后' in engine.store.rows('SELECT text FROM drafts')[0]['text']
    assert len(seen)==2 and seen[1]['avoid_phrases']==['我记下了']
    assert not engine.store.rows('SELECT * FROM operations')
    await engine.platform.close()


async def test_old_draft_is_checked_before_publish_outbox(engine):
    seed_video(engine)
    engine.store.execute("UPDATE drafts SET text='我记下了这个变化'")
    async def forbidden(*args): raise AssertionError('不能发送')
    engine.platform.publish_operation = forbidden
    with pytest.raises(PolicyBlocked, match='避免使用'):
        await engine.interaction.publish('d1', manual=True)
    assert not engine.store.rows('SELECT * FROM operations')
    await engine.platform.close()


async def test_style_change_is_rechecked_at_actual_send_gate(engine):
    from bililife.vendor.client import PublishNotSent
    seed_video(engine)
    async def prepared(metadata, text, gate, *args):
        assert gate()
        engine.settings = engine.settings.model_copy(update={'comment_avoid_phrases':['这个变化']})
        assert gate() is False
        raise PublishNotSent('风格设置已变更，尚未发送')
    engine.platform.publish_operation = prepared
    with pytest.raises(PublishNotSent): await engine.interaction.publish('d1', manual=True)
    assert engine.store.rows('SELECT state FROM operations')[0]['state'] == 'not_sent'
    await engine.platform.close()

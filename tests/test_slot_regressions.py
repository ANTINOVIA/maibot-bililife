import json
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest

from bililife.maibot_adapter import ModelUnavailable
from bililife.schemas import PlanProposal
from bililife.storage import dump
from bililife.configuration import Settings
from bililife.webui_service import WebUIService
from bililife.task_executor import TaskExecutor
from test_invariants import seed_video


def fixed(engine, monkeypatch, hour=10):
    now = engine.now().replace(hour=hour, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(time, 'time', lambda: now.timestamp())
    engine.now = lambda: now
    engine.settings = Settings(active_start='00:00', active_end='23:59', review_enabled=False, replies_enabled=False)
    return now


def proposal(*types):
    return PlanProposal(theme='今天', tasks=[{'type': kind, 'goal':'安排', 'reason':'感兴趣', 'query':'科学'} for kind in types])


async def test_failed_revision_preserves_plan_and_reports_error(engine, monkeypatch):
    fixed(engine, monkeypatch)
    async def good(*args): return proposal('rest')
    engine.host.generate = good
    old = (await engine.planner.create())['plan_id']
    async def bad(*args): raise ModelUnavailable('模型调用超时 token=sk-abcdefghijk')
    engine.host.generate = bad
    with pytest.raises(ModelUnavailable): await engine.planner.create(revise=True)
    assert engine.store.rows('SELECT id FROM plans WHERE active=1') == [{'id': old}]
    assert '模型调用超时' in engine.store.get('plan_error')
    assert 'sk-abcdefghijk' not in engine.store.get('plan_error')
    await engine.platform.close()


async def test_legacy_succeeded_rest_does_not_block_and_versions_increase(engine, monkeypatch):
    now = fixed(engine, monkeypatch)
    payload = {'degraded': True, 'theme': '保守休息：待模型/计划恢复'}
    task = {'id':'legacy-rest', 'type':'rest', 'earliest':now.timestamp()-1, 'deadline':now.timestamp()+3600, 'duration':30, 'dependencies':[], 'payload':{}}
    old = engine.store.save_plan(engine.day(), payload, [task], 'old')
    engine.store.execute("UPDATE tasks SET state='succeeded' WHERE id='legacy-rest'")
    engine.store.cleanup_legacy_plans()
    assert not engine.rest_until()
    board = await WebUIService(engine).snapshot('schedule_board')
    assert all(row['id'] != 'legacy-rest' for row in board['items'])
    assert engine.store.rows('SELECT active FROM plans WHERE id=?',(old,))[0]['active'] == 0
    async def good(*args): return proposal('rest')
    engine.host.generate=good
    await engine.planner.create()
    assert engine.store.rows('SELECT version FROM plans WHERE active=1')[0]['version'] == 2
    assert engine.store.rows("SELECT state FROM tasks WHERE id='legacy-rest'")[0]['state'] == 'succeeded'
    await engine.platform.close()


async def test_occupied_slots_and_manual_conflict(engine, monkeypatch):
    now=fixed(engine, monkeypatch)
    await engine.enqueue('memory', {}, earliest=now.timestamp(), deadline=now.timestamp()+900)
    with pytest.raises(ValueError, match='冲突'):
        await engine.enqueue('rest', {}, earliest=now.timestamp()+100, deadline=now.timestamp()+1000)
    slots=engine.planner.validate(proposal('rest'),now)
    assert slots[0]['earliest'] >= now.timestamp()+900
    await engine.platform.close()


async def test_auto_generation_backoff_survives_executor_restart(engine, monkeypatch):
    now=fixed(engine, monkeypatch)
    engine.store.set('settings', engine.settings.model_dump())
    await engine.schedule()
    retry=engine.store.get('plan_retry')
    assert retry['attempts']==1 and retry['next']>now.timestamp()
    engine.store.execute("UPDATE tasks SET state='failed' WHERE type='plan'")
    restarted = TaskExecutor(SimpleNamespace(), engine.data, engine.runtime)
    restarted.now = lambda: now
    await restarted.schedule()
    assert len(restarted.store.rows("SELECT id FROM tasks WHERE type='plan'"))==1
    assert restarted.store.get('plan_retry')==retry
    await restarted.platform.close()
    await engine.platform.close()


def test_late_day_omits_unfittable_and_dependents(engine, monkeypatch):
    now=fixed(engine, monkeypatch, 23)
    engine.settings=Settings(active_start='00:00',active_end='23:30',review_enabled=False)
    p=PlanProposal(theme='今天',tasks=[{'type':'discover','goal':'a','reason':'a','query':'科学'},
        {'type':'discover','goal':'b','reason':'b','query':'科学'},
        {'type':'memory','goal':'c','reason':'c','depends_on':[1]}])
    tasks=engine.planner.validate(p,now)
    assert len(tasks)==1 and len(engine.planner.omitted)==2
    assert engine.planner.omitted[-1]['reason']=='依赖活动未能安排'

async def test_succeeded_rest_reserves_slot_until_deadline(engine, monkeypatch):
    now = fixed(engine, monkeypatch)
    task_id = await engine.enqueue('rest', {}, earliest=now.timestamp(), deadline=now.timestamp()+3600)
    engine.store.execute("UPDATE tasks SET state='succeeded' WHERE id=?", (task_id,))
    with pytest.raises(ValueError, match='冲突'):
        await engine.enqueue('memory', {}, earliest=now.timestamp()+100, deadline=now.timestamp()+500)
    assert engine.planner.validate(proposal('memory'), now)[0]['earliest'] >= now.timestamp()+3600
    await engine.platform.close()


async def test_child_cannot_exceed_or_forge_root_slot(engine, monkeypatch):
    now = fixed(engine, monkeypatch)
    root = await engine.enqueue('discover', {'query':'科学'}, earliest=now.timestamp(), deadline=now.timestamp()+600)
    with pytest.raises(ValueError, match='内部活动归属'):
        await engine.enqueue('memory', {'_slot_owner':root})
    with pytest.raises(ValueError, match='所属活动'):
        engine.store.add_task('memory', {'_slot_owner':root}, engine.day(), now.timestamp(), now.timestamp()+601)
    await engine.platform.close()


def test_save_plan_rejects_overlapping_candidates(engine, monkeypatch):
    now = fixed(engine, monkeypatch).timestamp()
    def item(name, start):
        return {'id':name,'type':'rest','earliest':start,'deadline':start+3600,'duration':30,'dependencies':[], 'payload':{}}
    with pytest.raises(ValueError, match='互相冲突'):
        engine.store.save_plan(engine.day(), {}, [item('one',now),item('two',now+30)], 'test')
    assert not engine.store.rows('SELECT * FROM plans')


async def test_publish_conflict_defers_without_outbox_or_control_timeline(engine, monkeypatch):
    now = fixed(engine, monkeypatch)
    seed_video(engine)
    engine.settings = Settings(active_start='00:00', active_end='23:59', auto_publish_comments=True)
    engine.store.execute("INSERT INTO draft_origins VALUES('d1','agent')")
    await engine.enqueue('memory', {}, earliest=now.timestamp(), deadline=now.timestamp()+1800)
    control = await engine.enqueue('reconcile', {}, earliest=now.timestamp(), deadline=now.timestamp()+1800)
    assert await engine.interaction.queue_automatic() == []
    assert await engine.interaction.queue_automatic() == []
    with pytest.raises(ValueError, match='冲突'):
        await WebUIService(engine).publish('d1')
    assert not engine.store.rows("SELECT * FROM tasks WHERE type='publish'")
    assert not engine.store.rows('SELECT * FROM operations')
    assert engine.store.rows("SELECT status FROM drafts WHERE id='d1'")[0]['status'] == 'draft'
    board = await WebUIService(engine).snapshot('schedule_board')
    assert all(item['id'] != control for item in board['items'])
    await engine.platform.close()

async def test_board_lists_errors_and_root_detail_shows_failed_child(engine, monkeypatch):
    now = fixed(engine, monkeypatch)
    root = await engine.enqueue('discover', {'query':'科学'}, earliest=now.timestamp(), deadline=now.timestamp()+600)
    child = engine.store.add_task('watch', {'query':'BV1xx411c7mD','_slot_owner':root}, engine.day(), now.timestamp(), now.timestamp()+600, dependencies=[root])
    engine.store.execute("UPDATE tasks SET state='succeeded' WHERE id=?", (root,))
    engine.store.execute("UPDATE tasks SET state='failed',error='读取失败' WHERE id=?", (child,))
    service = WebUIService(engine)
    board = await service.snapshot('schedule_board')
    assert any(item['id']==root and item['activity_partial'] for item in board['items'])
    assert any(item['id']==child and item['error']=='读取失败' for item in board['issues'])
    assert all(item['id']!=child for item in board['items'])
    detail = await service.detail('schedule', root)
    assert detail['children'][0]['id']==child and detail['children'][0]['state']=='failed'
    await engine.platform.close()


def test_overnight_active_window_uses_either_daily_segment(engine, monkeypatch):
    now = fixed(engine, monkeypatch, 1)
    engine.settings = Settings(active_start='23:00', active_end='06:00')
    task = engine.planner.validate(proposal('discover'), now)[0]
    assert task['earliest'] == now.timestamp()

async def test_child_retry_expires_at_parent_deadline(engine, monkeypatch):
    now = fixed(engine, monkeypatch)
    root = await engine.enqueue('discover', {'query':'科学'}, earliest=now.timestamp(), deadline=now.timestamp()+600)
    engine.current = engine.store.claim(engine.owner)
    child = await engine.enqueue('watch', {'query':'BV1xx411c7mD'}, child=True)
    engine.store.finish(engine.current, 'succeeded')
    engine.store.execute("UPDATE tasks SET state='retry',next_run=? WHERE id=?", (now.timestamp()+601, child))
    monkeypatch.setattr(time, 'time', lambda: now.timestamp()+601)
    assert engine.store.claim(engine.owner) is None
    assert engine.store.rows('SELECT state FROM tasks WHERE id=?', (child,))[0]['state']=='expired'
    await engine.platform.close()

async def test_deleting_succeeded_root_cancels_queued_and_running_children(engine, monkeypatch):
    import asyncio
    now = fixed(engine, monkeypatch)
    root = await engine.enqueue('discover', {'query':'科学'}, earliest=now.timestamp(), deadline=now.timestamp()+600)
    child1 = engine.store.add_task('watch', {'query':'BV1xx411c7mD','_slot_owner':root}, engine.day(), now.timestamp(), now.timestamp()+600, dependencies=[root])
    child2 = engine.store.add_task('memory', {'_slot_owner':root}, engine.day(), now.timestamp(), now.timestamp()+600, dependencies=[root])
    engine.store.execute("UPDATE tasks SET state='succeeded' WHERE id=?", (root,))
    engine.current = engine.store.claim(engine.owner)
    assert engine.current['id'] == child1
    engine.running_job = asyncio.create_task(asyncio.sleep(60))
    await WebUIService(engine).schedule_change('delete', json.dumps({'task_id':root,'reason':'取消活动'}))
    await asyncio.gather(engine.running_job, return_exceptions=True)
    rows = {r['id']:r for r in engine.store.rows('SELECT id,state,cancel_requested FROM tasks')}
    assert rows[child1]['cancel_requested'] == 1 and child1 in engine.cancelled
    assert rows[child2]['state'] == 'cancelled'
    await engine.platform.close()


async def test_cancelling_root_propagates_to_children(engine, monkeypatch):
    now = fixed(engine, monkeypatch)
    root = await engine.enqueue('discover', {'query':'科学'}, earliest=now.timestamp(), deadline=now.timestamp()+600)
    child = engine.store.add_task('memory', {'_slot_owner':root}, engine.day(), now.timestamp(), now.timestamp()+600, dependencies=[root])
    engine.store.execute("UPDATE tasks SET state='succeeded' WHERE id=?", (root,))
    await WebUIService(engine).task_action(root, 'cancel')
    assert engine.store.rows('SELECT state FROM tasks WHERE id=?',(child,))[0]['state']=='cancelled'
    await engine.platform.close()


@pytest.mark.parametrize('state', ['running','unknown'])
async def test_group_with_inflight_publish_cannot_be_cancelled_or_deleted(engine, monkeypatch, state):
    now = fixed(engine, monkeypatch)
    root = await engine.enqueue('discover', {'query':'科学'}, earliest=now.timestamp(), deadline=now.timestamp()+600)
    child = engine.store.add_task('publish', {'draft_id':'d1','_slot_owner':root}, engine.day(), now.timestamp(), now.timestamp()+600, dependencies=[root])
    engine.store.execute('UPDATE tasks SET state=? WHERE id=?', (state, child))
    web=WebUIService(engine)
    with pytest.raises(ValueError, match='结果未知'):
        await web.schedule_change('delete', json.dumps({'task_id':root,'reason':'取消活动'}))
    with pytest.raises(ValueError, match='结果未知'):
        await web.task_action(root, 'cancel')
    assert engine.store.rows('SELECT state FROM tasks WHERE id=?', (child,))[0]['state']==state
    assert not engine.store.rows('SELECT * FROM task_tombstones')
    await engine.platform.close()


async def test_transport_failure_records_sanitized_error_and_preserves_plan(engine, monkeypatch):
    import httpx
    fixed(engine, monkeypatch)
    async def good(*args): return proposal('rest')
    engine.host.generate = good
    old = (await engine.planner.create())['plan_id']
    async def fail(*args): raise httpx.ConnectError('network token=sk-abcdefghijk')
    engine.host.generate = fail
    with pytest.raises(httpx.ConnectError): await engine.planner.create(revise=True)
    assert engine.store.rows('SELECT id FROM plans WHERE active=1') == [{'id':old}]
    assert 'ConnectError' in engine.store.get('plan_error')
    assert 'sk-abcdefghijk' not in engine.store.get('plan_error')
    await engine.platform.close()

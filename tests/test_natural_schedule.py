from types import SimpleNamespace
import time

import pytest

from bililife.configuration import Settings
from bililife.interaction_service import PolicyBlocked
from bililife.schemas import PlanProposal, ScheduleAdjustment
from bililife.task_executor import TaskExecutor
from bililife.webui_service import WebUIService
from test_invariants import seed_video
from test_schedule_comment_style import fixed_morning


def proposal(now, types):
    return PlanProposal(theme='闲时看看',tasks=[{'type':kind,'earliest_start':now.isoformat(),
        'deadline':now.replace(hour=22).isoformat(),'goal':'生活安排','reason':'留足休息','query':'科学'} for kind in types])


async def test_activity_types_have_different_windows_and_free_gaps(engine, monkeypatch):
    now=fixed_morning(engine,monkeypatch)
    result=engine.planner.validate(proposal(now,('discover','replies','rest','read_comments')),now)
    assert [r['deadline']-r['earliest'] for r in result]==[1800,900,10800,1800]
    assert result[1]['earliest']-result[0]['deadline']==1800
    assert result[2]['earliest']==result[1]['deadline']
    assert result[3]['earliest']==result[2]['deadline']
    assert all(r['duration']<=engine.config().task_timeout_seconds for r in result)
    await engine.platform.close()


def test_default_rest_is_longer_than_browsing_and_still_configurable():
    cfg=Settings()
    assert cfg.schedule_window_seconds('rest',10)==3600
    assert cfg.schedule_window_seconds('rest',36000)==10800
    assert cfg.schedule_window_seconds('discover',10)==900
    assert cfg.schedule_window_seconds('discover',36000)==1800
    assert cfg.schedule_window_seconds('replies',36000)==900
    for bad in ({'schedule_rest_minutes':10},{'schedule_rest_minutes':180,'schedule_rest_max_minutes':60},{'schedule_brief_minutes':31}):
        with pytest.raises(ValueError):Settings(**bad)


async def test_rest_blocks_new_browsing_and_publishing_and_survives_restart(engine,monkeypatch):
    now=fixed_morning(engine,monkeypatch)
    seed_video(engine)
    engine.settings=Settings(active_start='00:00',active_end='23:59',auto_publish_comments=True)
    engine.store.set('settings',engine.settings.model_dump())
    rest=engine.store.add_task('rest',{},engine.day(),now.timestamp()-1,now.timestamp()+3600)
    engine.store.execute("UPDATE tasks SET state='succeeded' WHERE id=?",(rest,))
    watch=engine.store.add_task('watch',{'query':'BV1xx411c7mD'},engine.day(),now.timestamp()-2,now.timestamp()+7200)
    async def forbidden(*args,**kwargs):raise AssertionError('休息中不得开始外部动作')
    engine.platform.metadata=forbidden
    assert engine.rest_until()==now.timestamp()+3600
    assert not engine.activity_allowed()
    assert engine.store.claim(engine.owner,active=engine.activity_allowed()) is None
    # An already claimed task also checks the persisted rest before dispatch.
    await engine.run(engine.store.claim(engine.owner))
    row=engine.store.rows('SELECT * FROM tasks WHERE id=?',(watch,))[0]
    assert row['state']=='retry' and row['next_run']==now.timestamp()+3600
    with pytest.raises(PolicyBlocked):await engine.interaction.publish('d1',manual=True)
    assert not await engine.interaction.queue_automatic()
    assert not engine.store.rows('SELECT * FROM operations') and not engine.store.rows('SELECT * FROM budget')
    restarted=TaskExecutor(SimpleNamespace(),engine.data,engine.runtime)
    assert restarted.rest_until()==now.timestamp()+3600
    assert (await WebUIService(restarted).snapshot('overview'))['rest_until']==now.timestamp()+3600
    await WebUIService(restarted).schedule_change('delete','{"task_id":"'+rest+'","reason":"结束这次休息"}')
    assert restarted.activity_allowed()
    for e in (engine,restarted):await e.platform.close()


async def test_adaptive_insert_moves_after_booked_rest(engine,monkeypatch):
    now=fixed_morning(engine,monkeypatch)
    engine.store.add_task('rest',{},engine.day(),now.timestamp(),now.timestamp()+7200)
    tasks=engine.planner.validate(ScheduleAdjustment(reason='晚些再看',tasks=proposal(now,('discover',)).tasks),now)
    assert tasks[0]['earliest']==now.timestamp()+7200
    assert tasks[0]['deadline']-tasks[0]['earliest']==1800
    await engine.platform.close()

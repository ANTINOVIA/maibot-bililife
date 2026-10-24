from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import timedelta
from zoneinfo import ZoneInfo
import json
import time

import pytest

from bililife.configuration import Settings
from bililife.schemas import Selection
from bililife.schemas import ScheduleAdjustment
from bililife.storage import BudgetExceeded, Storage, dump
from bililife.webui_service import WebUIService
from test_invariants import seed_video


def test_once_budget_is_atomic_persistent_and_day_scoped(store):
    resources = {'videos': (1, 1), 'browse_seconds': (120, 120)}
    with ThreadPoolExecutor(max_workers=5) as pool:
        assert sum(pool.map(lambda _: store.reserve_once('today', 'watch:v1', resources), range(12))) == 1
    restarted = Storage(store.directory)
    assert restarted.reserve_once('today', 'watch:v1', resources) is False
    with pytest.raises(BudgetExceeded):
        restarted.reserve_once('today', 'watch:v2', resources)
    assert not restarted.rows("SELECT * FROM budget_reservations WHERE reservation_key='watch:v2'")
    assert restarted.reserve_once('tomorrow', 'watch:v1', resources)
    assert restarted.rows("SELECT used FROM budget WHERE day='today' ORDER BY resource") == [{'used': 120}, {'used': 1}]


async def test_requeue_failed_watch_reuses_video_allowance_but_records_actual_calls(engine):
    metadata = seed_video(engine)
    engine.store.execute('DELETE FROM drafts')
    engine.store.execute('DELETE FROM videos')
    engine.settings = Settings(max_videos=1, max_browse_seconds=120, comments_enabled=False,
                               memory_enabled=False, adaptive_schedule_enabled=False)
    now = time.time()
    task_id = engine.store.add_task('watch', {'query': metadata['bvid']}, engine.day(), now-1, now+300)
    async def info(_): return metadata
    async def select(*args, **kwargs):
        engine.store.reserve(engine.day(), {'model_calls': (1, 10)})
        return Selection(browse=True, reason='感兴趣', topic='科学')
    attempts = []
    async def analyze(*args):
        attempts.append(1)
        if len(attempts) == 1: raise ValueError('引用失败')
        return {'types': ['subtitles'], 'coverage': '部分字幕'}, {'summary': '真实摘要'}
    engine.platform.metadata, engine.host.generate = info, select
    engine.understanding.analyze = analyze
    await engine.run(engine.store.claim(engine.owner))
    assert engine.store.rows('SELECT state FROM tasks')[0]['state'] == 'failed'
    before = engine.store.rows('SELECT * FROM budget ORDER BY resource')
    await WebUIService(engine).task_action(task_id, 'resume')
    assert engine.store.rows('SELECT * FROM budget ORDER BY resource') == before
    await engine.run(engine.store.claim(engine.owner))
    assert engine.store.rows('SELECT state FROM tasks')[0]['state'] == 'succeeded'
    budget = {r['resource']: r['used'] for r in engine.store.rows('SELECT * FROM budget')}
    assert budget == {'videos': 1, 'browse_seconds': 120, 'model_calls': 2}
    await engine.platform.close()


def test_legacy_evidence_is_migrated_without_changing_budget(store):
    store.execute('DELETE FROM schema_version WHERE version=5')
    created = time.time()
    day = datetime.fromtimestamp(created, ZoneInfo('Asia/Shanghai')).date().isoformat()
    metadata = {'duration': 120}
    store.execute('INSERT INTO videos VALUES(?,?,?,?,?,?,?,?,?)',
                  ('v1', 'BV1xx411c7mD', 1, dump(metadata), dump({'types': ['metadata', 'subtitles']}), '{}', 'failed', '', created))
    store.reserve(day, {'videos': (1, 1), 'browse_seconds': (120, 120)})
    before = store.rows('SELECT * FROM budget ORDER BY resource')
    restarted = Storage(store.directory)
    assert not restarted.reserve_once(day, 'watch:v1', {'videos': (1, 1), 'browse_seconds': (120, 120)})
    assert restarted.rows('SELECT * FROM budget ORDER BY resource') == before


async def test_requeued_watch_does_not_reserve_another_slot_in_adaptive_plan(engine):
    metadata=seed_video(engine)
    engine.settings=Settings(max_videos=2)
    engine.active=lambda *args:True
    day=engine.day()
    engine.store.reserve_once(day,'watch:'+metadata['bvid']+':p1',{'videos':(1,2),'browse_seconds':(120,1200)})
    engine.store.add_task('watch',{'query':metadata['bvid']},day,time.time()+1,time.time()+300)
    async def generate(*args,**kwargs):
        now=engine.now()
        return ScheduleAdjustment(reason='继续探索',tasks=[{'type':'explore','query':'科学 演示',
            'earliest_start':(now+timedelta(seconds=2)).isoformat(),
            'deadline':(now+timedelta(seconds=40)).isoformat(),'goal':'探索','reason':'新问题'}])
    engine.host.generate=generate
    assert len((await engine.planner.adapt('v1'))['inserted'])==1
    await engine.platform.close()

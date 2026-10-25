from datetime import timedelta
from types import SimpleNamespace
import asyncio
import json
import time
import tomllib

import pytest

from bililife.configuration import Settings, migrate_settings
from bililife.config_io import parse_settings_document, settings_document
from bililife.interaction_service import PolicyBlocked
from bililife.module_settings import MODULES
from bililife.schemas import Selection
from bililife.storage import dump, Storage
from bililife.task_executor import TaskExecutor
from bililife.webui_service import WebUIService


@pytest.mark.parametrize('mode,confirmed,enabled,automatic',[
    ('observe_only',False,False,False),('draft_only',False,True,False),
    ('autonomous',True,True,True),('autonomous',False,True,False)])
def test_old_modes_migrate_once_without_widening_publishing(mode,confirmed,enabled,automatic):
    cfg=Settings.model_validate(migrate_settings({'mode':mode,'autonomous_limits_confirmed':confirmed}))
    assert cfg.comments_enabled is enabled and cfg.replies_enabled is enabled
    assert cfg.auto_publish_comments is automatic and cfg.auto_publish_replies is automatic
    assert 'mode' not in cfg.model_dump() and 'mode' not in Settings.model_json_schema()['properties']


def test_modules_cover_all_settings_and_toml_round_trip():
    keys=[k for module in MODULES.values() for k in module['fields']]
    assert len(keys)==len(set(keys))
    assert set(keys)==set(Settings.model_fields)
    cfg=Settings(persona_guidance='倾向简短表达\n多了解天文',expression_preferences=['用中文','少用感叹号'],prompt_overrides={'plan':'保留休息\n允许不互动'})
    text=settings_document(cfg)
    assert Settings.model_validate(parse_settings_document(text))==cfg
    assert not any(line.startswith('mode =') for line in text.splitlines()) and 'api_key' not in text


async def test_partial_settings_concurrent_updates_keep_other_modules(engine):
    web=WebUIService(engine)
    await asyncio.gather(web.settings_patch('browse',dump({'max_videos':6})),web.settings_patch('replies',dump({'max_replies':4})))
    assert engine.config().max_videos==6 and engine.config().max_replies==4
    stored=parse_settings_document((engine.data/'settings.toml').read_text(encoding='utf-8'))
    assert stored['max_videos']==6 and stored['max_replies']==4
    with pytest.raises(ValueError,match='跨模块'):
        await web.settings_patch('browse',dump({'auto_publish_comments':True}))
    await engine.platform.close()


async def test_toml_editor_validation_and_restart(engine):
    web=WebUIService(engine)
    await web.settings_toml_save('browse','[browse]\nmax_videos = 7\nallowed_topics = ["科学", "天文"]\n')
    with pytest.raises(ValueError):
        await web.settings_toml_save('browse','[browse]\nmax_videos = -1\n')
    assert engine.config().max_videos==7
    with pytest.raises(ValueError,match='跨模块'):
        await web.settings_toml_save('browse','[replies]\nmax_replies = 5\n')
    file=engine.data/'settings.toml'
    text=file.read_text(encoding='utf-8').replace('max_videos = 7','max_videos = 8')
    file.write_text(text,encoding='utf-8')
    restarted=TaskExecutor(SimpleNamespace(),engine.data,engine.runtime)
    assert restarted.config().max_videos==8
    file.write_text('[browse]\nmax_videos = "invalid"\n',encoding='utf-8')
    invalid=TaskExecutor(SimpleNamespace(),engine.data,engine.runtime)
    assert invalid.config().max_videos==8
    assert invalid.store.get('config_error')
    file.write_text('[schedule]\ntimezone = "Not/A/Timezone"\n',encoding='utf-8')
    bad_zone=TaskExecutor(SimpleNamespace(),engine.data,engine.runtime)
    assert bad_zone.config().timezone=='Asia/Shanghai' and bad_zone.store.get('config_error')
    for item in (engine,restarted,invalid,bad_zone):
        await item.platform.close()


async def test_modules_disable_running_work_and_separate_comment_reply_flags(engine):
    web=WebUIService(engine)
    await web.settings_patch('comments',dump({'auto_publish_comments':True}))
    assert engine.config().auto_publish_comments and not engine.config().auto_publish_replies
    await web.settings_patch('comments',dump({'comments_enabled':False}))
    assert not engine.config().auto_publish_comments
    await web.settings_patch('browse',dump({'browsing_enabled':False}))
    with pytest.raises(PolicyBlocked):
        await engine.dispatch('watch',{'query':'BV1xx411c7mD'})
    await engine.platform.close()


async def test_manual_interest_does_not_disable_all_automatic_evolution_for_day(engine):
    from bililife.schemas import Evolution
    await WebUIService(engine).interest_save(dump({'topic':'科学','weight':.7,'frozen':False,'reason':'人工偏好'}))
    async def generate(*args):
        return Evolution(changes=[{'topic':'科学','delta':.02,'reason':'三个独立来源持续感兴趣'}])
    engine.host.generate=generate
    result=await engine.evolution.evolve(engine.day(),[{'bvid':str(i),'id':str(i)} for i in range(3)])
    assert len(result['changes'])==1
    assert engine.store.rows('SELECT weight FROM interests WHERE topic=?',('科学',))[0]['weight']==.72
    await engine.platform.close()


async def test_manual_interest_and_persona_interventions_are_versioned_and_reversible(engine):
    web=WebUIService(engine)
    r=await web.interest_save(dump({'topic':'天文','weight':.85,'frozen':True,'reason':'这周想多了解星空'}))
    row=engine.store.rows('SELECT * FROM interests WHERE topic=?',('天文',))[0]
    assert row['weight']==.85 and row['frozen']==1 and '天文' in engine.config().allowed_topics
    assert engine.store.rows('SELECT sources FROM persona_changes')[0]['sources']=='["administrator"]'
    await web.interest_action(r['change_id'],'rollback')
    assert engine.store.rows('SELECT weight FROM interests WHERE topic=?',('天文',))[0]['weight']==.5
    await web.settings_patch('persona',dump({'persona_guidance':'这周多看科学演示','expression_preferences':['简短表达']}))
    first=engine.store.rows('SELECT * FROM persona_interventions')[0]
    async def profile(*args):
        return {'personality':'宿主身份','reply_style':'宿主表达'}
    engine.host.ctx=SimpleNamespace(config=SimpleNamespace(get=profile))
    persona=await engine.host.persona()
    assert persona['personality']=='宿主身份' and persona['bililife_user_guidance']=='这周多看科学演示'
    await web.interest_action(first['id'],'rollback_guidance')
    assert engine.config().persona_guidance=='' and not engine.config().expression_preferences
    assert engine.store.rows('SELECT rolled_back FROM persona_interventions WHERE id=?',(first['id'],))[0]['rolled_back']==1
    await engine.platform.close()


async def test_schedule_insert_delete_restore_preserves_records_and_restart(engine):
    web=WebUIService(engine)
    now=engine.now()
    target=now+timedelta(days=1)
    result=await web.schedule_change('insert',dump({'kind':'rest','date':target.date().isoformat(),'start':'10:00','end':'10:15','duration':60,'reason':'手动休息'}))
    task_id=result['task_id']
    record=engine.store.rows('SELECT * FROM tasks WHERE id=?',(task_id,))[0]
    assert record['day']==target.date().isoformat() and record['state']=='queued'
    board=await web.snapshot('schedule_board:'+target.date().isoformat())
    assert len(board['items'])==1 and board['items'][0]['id']==task_id and not board['is_today']
    await web.schedule_change('delete',dump({'task_id':task_id,'reason':'调整安排'}))
    assert engine.store.rows('SELECT state FROM tasks WHERE id=?',(task_id,))[0]['state']=='cancelled'
    assert engine.store.rows('SELECT * FROM task_tombstones WHERE task_id=?',(task_id,))
    engine.store.recover()
    await web.schedule_change('restore',dump({'task_id':task_id}))
    assert not engine.store.rows('SELECT * FROM task_tombstones WHERE task_id=?',(task_id,))
    assert engine.store.rows('SELECT state FROM tasks WHERE id=?',(task_id,))[0]['state']=='cancelled'
    assert len(engine.store.rows('SELECT * FROM schedule_edits'))==2
    assert 2 in [x['version'] for x in engine.store.rows('SELECT * FROM schema_version')]
    await engine.platform.close()


async def test_disabling_browsing_cancels_running_job_before_next_external_action(engine):
    task_id=engine.store.add_task('watch',{'query':'BV1xx411c7mD'},engine.day(),time.time()-1,time.time()+300)
    task=engine.store.claim(engine.owner)
    entered=asyncio.Event()
    calls=[]
    async def dispatch(*args):
        entered.set()
        await asyncio.Future()
        calls.append('external action')
    engine.dispatch=dispatch
    running=asyncio.create_task(engine.run(task))
    await entered.wait()
    await WebUIService(engine).settings_patch('browse',dump({'browsing_enabled':False}))
    await asyncio.wait_for(running,3)
    assert not calls
    assert engine.store.rows('SELECT state FROM tasks WHERE id=?',(task_id,))[0]['state']=='paused'
    await engine.platform.close()


async def test_schedule_window_and_payload_invalid_do_not_insert(engine):
    web=WebUIService(engine)
    day=(engine.now()+timedelta(days=1)).date().isoformat()
    for args in [dict(kind='watch',date=day,start='01:00',end='01:30',payload={'query':'BV1xx411c7mD'}),dict(kind='rest',date=day,start='10:00',end='09:00'),dict(kind='rest',date=day,start='10:00',end='11:00',payload={'shell':'whoami'})]:
        with pytest.raises(ValueError):
            await web.schedule_change('insert',dump(args))
    assert not engine.store.rows('SELECT * FROM tasks')
    await engine.platform.close()


async def test_unknown_publication_cannot_be_deleted(engine):
    task=engine.store.add_task('publish',{},engine.day(),time.time(),time.time()+300)
    engine.store.execute("UPDATE tasks SET state='unknown' WHERE id=?",(task,))
    with pytest.raises(ValueError,match='核对'):
        await WebUIService(engine).schedule_change('delete',dump({'task_id':task}))
    await engine.platform.close()


async def test_schedule_day_query_has_all_tasks_not_first_page(engine):
    for i in range(30):
        engine.store.add_task('rest',{'reason':str(i)},engine.day(),time.time()+i,time.time()+500)
    result=await WebUIService(engine).snapshot('schedule_board')
    assert len(result['items'])==30
    assert result['timezone']=='Asia/Shanghai'
    await engine.platform.close()

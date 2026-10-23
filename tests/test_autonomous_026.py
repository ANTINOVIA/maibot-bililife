from datetime import timedelta
from types import SimpleNamespace
import asyncio
import json
import time

import pytest

from bililife.configuration import Settings, migrate_settings
from bililife.schemas import DiscoveryChoice, Selection, ScheduleAdjustment, Draft, InteractionDecision
from bililife.storage import dump
from bililife.task_executor import TaskExecutor
from bililife.webui_service import WebUIService
from test_invariants import seed_video


def test_removed_video_override_and_fixed_concurrency_migrate():
    cfg=Settings.model_validate(migrate_settings({'allowed_videos':['BV1xx411c7mD'],'concurrency':1}))
    assert 'allowed_videos' not in cfg.model_dump() and 'concurrency' not in cfg.model_dump()
    for uid in ('0','-1','abc','１２３',str(2**64)):
        with pytest.raises(ValueError):Settings(owner_uid=uid)
    assert Settings(owner_uid='123').owner_uid=='123'


async def test_old_toml_migrates_without_losing_other_preferences(engine):
    path=engine.data/'settings.toml'
    path.write_text('[schedule]\nconcurrency=1\nplan_time="10:00"\n[browse]\nallowed_videos=["BV1xx411c7mD"]\nmax_videos=8\n',encoding='utf-8')
    new=TaskExecutor(SimpleNamespace(),engine.data,engine.runtime)
    assert new.config().plan_time=='10:00' and new.config().max_videos==8
    assert not new.store.get('config_error') and 'allowed_videos' not in path.read_text(encoding='utf-8')
    for e in (new,engine):await e.platform.close()


async def test_autonomous_discovery_needs_no_video_or_query_and_children_have_time(engine):
    engine.active=lambda *args:True
    seen=[]
    async def generate(stage,external,schema,*args):
        if schema is DiscoveryChoice:return DiscoveryChoice(query='科学 宇宙',reason='近期好奇')
        return Selection(browse=True,reason='感兴趣',topic='科学')
    async def discover(query,*args):
        seen.append(query)
        return [{'bvid':'BV1xx411c7mD','title':'宇宙','description':'','up_uid':'1'}]
    engine.host.generate=generate
    engine.platform.discover=discover
    task=engine.store.add_task('discover',{},engine.day(),time.time()-1,time.time()+10)
    engine.current=engine.store.claim(engine.owner)
    result=await engine.discover({})
    assert seen==['科学 宇宙'] and result['chosen']
    row=engine.store.rows("SELECT * FROM tasks WHERE type='watch'")[0]
    assert row['deadline']>engine.current['deadline'] and json.loads(row['dependencies'])==[task]
    await engine.platform.close()


async def test_agent_schedule_insert_is_persistent_idempotent_and_capped(engine):
    seed_video(engine)
    engine.active=lambda *args:True
    async def generate(*args):
        now=engine.now()
        return ScheduleAdjustment(reason='对这个现象有新问题',tasks=[{'type':'rest','earliest_start':(now+timedelta(seconds=2)).isoformat(),'deadline':(now+timedelta(seconds=40)).isoformat(),'goal':'休息','reason':'先消化已有经历'}])
    engine.host.generate=generate
    first=await engine.planner.adapt('v1')
    second=await engine.planner.adapt('v1')
    assert len(first['inserted'])==1 and not second['inserted']
    row=engine.store.rows('SELECT * FROM tasks')[0]
    assert json.loads(row['payload'])['origin']=='agent'
    assert engine.store.rows('SELECT state FROM adaptive_decisions')[0]['state']=='completed'
    engine.store.set('settings',engine.config().model_dump())
    restarted=TaskExecutor(SimpleNamespace(),engine.data,engine.runtime)
    assert restarted.store.rows('SELECT id FROM tasks')[0]['id']==first['inserted'][0]
    for e in (engine,restarted):await e.platform.close()


async def test_agent_insertion_respects_existing_reserved_video_budget(engine):
    seed_video(engine)
    engine.active=lambda *args:True
    engine.store.add_task('discover',{'max_items':4},engine.day(),time.time()+1,time.time()+200)
    async def generate(*args):
        now=engine.now()
        return ScheduleAdjustment(reason='想了解相关知识',tasks=[{'type':'explore','query':'科学 演示','earliest_start':(now+timedelta(seconds=2)).isoformat(),'deadline':(now+timedelta(seconds=40)).isoformat(),'goal':'探索','reason':'后续问题'}])
    engine.host.generate=generate
    with pytest.raises(ValueError,match='额度'):
        await engine.planner.adapt('v1')
    assert len(engine.store.rows('SELECT * FROM tasks'))==1
    await engine.platform.close()


async def test_automatic_comment_has_context_mentions_and_no_manual_confirmation(engine):
    metadata=seed_video(engine)
    engine.store.execute('DELETE FROM drafts')
    engine.settings=Settings(auto_publish_comments=True,owner_uid='456')
    captured=[]
    async def context(*args):return [{'text':'这是网友的意见，不是视频事实'}]
    async def mention(text,uid):return text+' @主人 ',{'uid':uid,'name':'主人'}
    async def generate(stage,data,schema,*args):
        assert data['comments'][0]['text']=='这是网友的意见，不是视频事实'
        if schema is InteractionDecision:return InteractionDecision(interact=True,reason='有新信息',evidence_quote='水在加热后发生变化')
        return Draft(text='这个变化出现的时间很有意思',reason='有依据',evidence_quote='水在加热后发生变化')
    async def send(metadata,text,gate,root,parent,**kwargs):
        assert gate() and kwargs['mentions']=={'主人':456}
        captured.append(text)
        return '777'
    engine.platform.comment_context=context
    engine.platform.owner_mention=mention
    engine.platform.publish_operation=send
    engine.host.generate=generate
    result=await engine.interaction.create_draft('v1')
    await engine.interaction.queue_automatic()
    assert len(engine.store.rows("SELECT id FROM tasks WHERE type='publish'"))==1
    assert (await engine.interaction.publish(result['draft_id'],manual=False))['status']=='succeeded'
    assert captured==['这个变化出现的时间很有意思 @主人 ']
    await engine.platform.close()


async def test_manual_drafts_are_not_swept_into_autopublish(engine):
    seed_video(engine)
    engine.settings=Settings(auto_publish_comments=True)
    assert not await engine.interaction.queue_automatic()
    assert not engine.store.rows('SELECT * FROM tasks')
    await engine.platform.close()


async def test_owner_mapping_uses_real_profile_and_uid(engine,monkeypatch):
    class Account:
        def __init__(self,uid,credential):assert uid==456
        async def get_user_info(self):return {'name':'主人的名字'}
    from bililife import bilibili_adapter
    monkeypatch.setattr(bilibili_adapter.user,'User',Account)
    async def read(stage,op):return await op
    engine.platform._read_stage=read
    text,mapping=await engine.platform.owner_mention('这段内容很有意思','456')
    assert text.endswith('@主人的名字 ') and mapping=={'uid':'456','name':'主人的名字'}
    await engine.platform.close()


async def test_owner_mention_is_in_actual_prepared_post(engine,monkeypatch):
    import httpx
    from urllib.parse import parse_qs
    from bililife.vendor import client
    class Request:
        def __init__(self,**kwargs):pass
        def update_data(self,**data):self.data=data
        async def _prepare_request(self):
            return {'url':'https://api.bilibili.com/x/v2/reply/add','params':{},'headers':{},'cookies':{},'data':self.data}
    monkeypatch.setattr(client,'Api',Request)
    captured=[]
    def respond(request):
        form=parse_qs(request.content.decode())
        assert json.loads(form['at_name_to_mid'][0])=={'主人':456}
        assert form['message']==['有趣的现象 @主人 ']
        captured.append(request.method)
        return httpx.Response(200,json={'code':0,'data':{'rpid':789}})
    await engine.platform.http.aclose()
    engine.platform.http=httpx.AsyncClient(transport=httpx.MockTransport(respond))
    engine.platform.credential=SimpleNamespace(get_cookies=lambda:{'SESSDATA':'fake-cookie','bili_jct':'fake-csrf'})
    result=await engine.platform.publish(SimpleNamespace(aid=1,bvid='BV1xx411c7mD'),'有趣的现象 @主人 ',lambda:True,mentions={'主人':456})
    assert result=='789' and captured==['POST']
    await engine.platform.close()

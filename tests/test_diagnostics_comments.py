from datetime import timedelta
from types import SimpleNamespace
import json
import time

import pytest

from bililife.configuration import Settings
from bililife.model_diagnostics import DiagnosticError
from bililife.schemas import CommentUnderstanding, PlanProposal, Selection
from bililife.webui_service import WebUIService
from bililife.storage import dump


async def test_diagnostics_ignore_zero_life_budgets_pause_and_storage(engine):
    engine.settings=Settings(max_model_calls=0,max_tokens=0,max_videos=0)
    engine.paused=True
    file=engine.data/'diagnostic.sqlite3-large'
    with file.open('wb') as stream:stream.seek(257*1024**2);stream.write(b'0')
    async def respond(*args,**kwargs):return {'success':True,'response':'{"ok":true}','model':'actual-model','usage':{'total_tokens':4}}
    engine.host.router.generate=respond
    job=await WebUIService(engine).model_test('text')
    task=engine.store.claim(engine.owner,paused=True,active=False)
    await engine.run(task)
    record=engine.store.get('model_provider_tests')['text']
    assert record['state']=='succeeded' and record['latency_ms']>=0
    assert record['model']=='actual-model' and record['budget_scope']=='diagnostic'
    assert engine.store.rows('SELECT * FROM budget')==[]
    assert engine.store.rows('SELECT state FROM tasks WHERE id=?',(job['task_id'],))[0]['state']=='succeeded'
    file.unlink()
    await engine.platform.close()


async def test_diagnostics_persist_http_error_code_without_leaking_details(engine):
    async def reject(*args,**kwargs):raise RuntimeError('HTTP 401 SESSDATA=must-not-leak')
    engine.host.router.generate=reject
    with pytest.raises(DiagnosticError):await engine.model_test('text')
    record=engine.store.get('model_provider_tests')['text']
    assert record['error_code']=='HTTP_401' and record['http_status']==401
    assert 'must-not-leak' not in dump(record) and record['latency_ms']>=0
    assert not engine.store.rows('SELECT * FROM budget')
    await engine.platform.close()


async def test_diagnostics_sdk_code_and_empty_asr_response(engine):
    async def denied(*args,**kwargs):return {'success':False,'error_code':'PROVIDER_UNAVAILABLE'}
    engine.host.router.generate=denied
    with pytest.raises(DiagnosticError):await engine.model_test('text')
    assert engine.store.get('model_provider_tests')['text']['error_code']=='PROVIDER_UNAVAILABLE'
    async def transcribe(audio):
        assert audio.startswith(b'RIFF')
        return {'success':True,'text':'','model':'asr'}
    engine.host.router.transcribe=transcribe
    assert (await engine.model_test('asr'))['state']=='succeeded'
    assert not engine.store.rows('SELECT * FROM budget')
    await engine.platform.close()


async def test_missing_video_diagnostic_dependency_is_explicit(engine,monkeypatch):
    monkeypatch.setattr('bililife.model_diagnostics.shutil.which',lambda _:None)
    engine.host.router.state['routes']['video']='custom'
    with pytest.raises(DiagnosticError):await engine.model_test('video')
    record=engine.store.get('model_provider_tests')['video']
    assert record['error_code']=='LOCAL_FFMPEG_MISSING'
    assert record['latency_ms'] is None
    await engine.platform.close()


async def test_repeat_click_returns_existing_diagnostic_job(engine):
    web=WebUIService(engine)
    a=await web.model_test('text')
    b=await web.model_test('text')
    assert a['task_id']==b['task_id']
    assert engine.store.get('model_provider_tests')['text']['state']=='queued'
    await engine.platform.close()


def seed_comments(engine,bad_quote=False):
    engine.settings=Settings(max_videos=0,max_browse_seconds=60,video_understanding_mode="frames",enable_asr=True)
    engine.active=lambda *args:True
    async def discover(*args):return [{'bvid':'BV1xx411c7mD','title':'科学话题','description':'讨论','up_uid':'1'}]
    async def metadata(*args):return {'bvid':'BV1xx411c7mD','aid':1,'page':1,'title':'科学话题','description':'讨论','up_uid':'1','url':'https://www.bilibili.com/video/BV1xx411c7mD','duration':9999}
    async def comments(*args,**kwargs):return [{'rpid':'11','text':'网友认为需要更多依据','author_uid':'7'}]
    async def forbidden(*args):raise AssertionError('只读评论不得读取媒体、字幕或触发发布')
    async def generate(stage,external,schema,*args):
        if schema is Selection:return Selection(browse=True,reason='对讨论好奇',topic='科学')
        return CommentUnderstanding(summary='网友讨论证据是否充分',points=[{'rpid':'11','quote':'伪造引用' if bad_quote else '需要更多依据','interpretation':'希望提供来源'}],feeling='想进一步了解',questions=['有哪些来源'],uncertainty='只读了一条评论')
    engine.platform.discover=discover
    engine.platform.metadata=metadata
    engine.platform.comment_context=comments
    engine.platform.subtitles=forbidden
    engine.platform.streams=forbidden
    engine.platform.publish_operation=forbidden
    engine.host.generate=generate


async def test_comment_only_activity_has_evidence_memory_and_never_watches(engine):
    seed_comments(engine)
    result=await engine.dispatch('read_comments',{'query':'科学'})
    assert result['watched'] is False and result['analysis']['suitable_for_video_comment'] is False
    assert not engine.store.rows('SELECT * FROM videos') and not engine.store.rows('SELECT * FROM drafts')
    assert engine.store.rows('SELECT resource,used FROM budget')==[{'resource':'comment_sessions','used':1}]
    row=engine.store.rows('SELECT * FROM comment_reads')[0]
    assert row['state']=='understood'
    assert (await engine.memory.search('网友讨论证据'))[0]['source'].endswith('#reply11')
    web=WebUIService(engine)
    assert (await web.snapshot('comment_reads'))['total']==1
    assert (await web.detail('comment_reads',row['id']))['comments'][0]['rpid']=='11'
    await engine.platform.close()


async def test_comment_only_quote_failure_is_real_error_and_retry_does_not_double_count(engine):
    seed_comments(engine,bad_quote=True)
    with pytest.raises(ValueError,match='引用'):await engine.dispatch('read_comments',{'query':'科学'})
    assert engine.store.rows('SELECT state FROM comment_reads')[0]['state']=='failed'
    seed_comments(engine)
    assert (await engine.dispatch('read_comments',{'query':'科学'}))['watched'] is False
    assert engine.store.rows('SELECT used FROM budget')[0]['used']==1
    await engine.platform.close()


async def test_comment_only_tasks_can_be_planned_and_inserted_with_no_video_budget(engine):
    engine.settings=Settings(max_videos=0,max_browse_seconds=60)
    engine.active=lambda *args:True
    now=engine.now()
    proposal=PlanProposal(theme='看看讨论',tasks=[{'type':'read_comments','earliest_start':now.isoformat(),'deadline':(now+timedelta(minutes=3)).isoformat(),'goal':'只读评论','query':'科学','reason':'对网友讨论好奇'}])
    assert engine.planner.validate(proposal,now)[0]['type']=='read_comments'
    date=(now+timedelta(days=1)).date().isoformat()
    inserted=await WebUIService(engine).schedule_change('insert',dump({'kind':'read_comments','date':date,'start':'10:00','end':'11:00','payload':{'query':'科学'}}))
    assert inserted['task_id']
    await engine.platform.close()


async def test_interrupted_comment_reading_recovers_without_duplicate_session_charge(engine):
    seed_comments(engine)
    await engine.dispatch('read_comments',{'query':'科学'})
    engine.store.execute("UPDATE comment_reads SET state='reading'")
    engine.store.recover()
    assert engine.store.rows('SELECT state FROM comment_reads')[0]['state']=='failed'
    await engine.dispatch('read_comments',{'query':'科学'})
    assert engine.store.rows('SELECT used FROM budget')[0]['used']==1
    assert engine.store.rows('SELECT state FROM comment_reads')[0]['state']=='understood'
    await engine.platform.close()

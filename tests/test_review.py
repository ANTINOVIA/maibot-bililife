import json
import time

from bililife.schemas import Review
from bililife.storage import dump


async def test_review_includes_cross_day_completion_and_preserves_revision(engine):
    now=time.time()
    metadata={'bvid':'BV1xx411c7mD','url':'https://www.bilibili.com/video/BV1xx411c7mD'}
    engine.store.execute('INSERT INTO videos VALUES(?,?,?,?,?,?,?,?,?)',('v',metadata['bvid'],1,dump(metadata),dump({'coverage':'部分字幕'}),dump({'summary':'实际抽样'}),'understood','',now-86400))
    task=engine.store.add_task('watch',{},'previous-day',now-86400,now+60)
    engine.store.execute("UPDATE tasks SET state='succeeded',result=?,updated=? WHERE id=?",(dump({'video_id':'v'}),now,task))
    async def generate(stage,data,schema):
        assert stage=='review'
        assert data['videos'][0]['id']=='v'
        assert data['videos'][0]['coverage']=='部分字幕'
        return Review(summary='午夜后完成部分字幕理解，来源保留',learned=[],unresolved=[],tomorrow=[])
    engine.host.generate=generate
    await engine.review()
    await engine.review()
    summary=engine.store.rows('SELECT payload FROM summaries')[0]
    assert json.loads(summary['payload'])['version']==2
    assert len(engine.store.rows("SELECT id FROM audit WHERE kind='review_revised'"))==1
    await engine.platform.close()


async def test_review_does_not_persist_repeated_unsupported_claims(engine):
    calls=[]
    async def generate(*args):
        calls.append(args)
        return Review(summary='自动字幕错得挺多。',learned=[],unresolved=[],tomorrow=[])
    engine.host.generate=generate
    result=await engine.review()
    assert len(calls)==2 and result['degraded'] is True
    assert '自动字幕错得挺多' not in engine.store.rows('SELECT text FROM summaries')[0]['text']
    assert engine.store.rows("SELECT id FROM audit WHERE kind='review_degraded'")
    await engine.platform.close()

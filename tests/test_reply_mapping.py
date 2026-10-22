from types import SimpleNamespace
import asyncio
import time

import pytest
from bilibili_api import comment
from bililife.storage import dump


async def test_thread_uses_actual_library_signature(engine,monkeypatch):
    captured=[]
    class Resource:
        def __init__(self,oid,type_,rpid,credential):
            captured.append((oid,type_,rpid,credential))
        async def get_sub_comments(self,page_size):
            return {'replies':[]}
    monkeypatch.setattr(comment,'Comment',Resource)
    assert await engine.platform.thread(123,456)=={'replies':[]}
    assert captured[0][:3]==(123,comment.CommentResourceType.VIDEO,456)
    await engine.platform.close()


async def test_reply_notification_only_matches_own_video_root_and_target(engine):
    aid=113702867177543
    engine.platform.uid='123'
    engine.platform.credential=object()
    now=time.time()
    row={'item':{'target_id':11,'root_id':9,'business_id':1,'subject_id':aid,'source_id':55,'source_content':'一个新的问题'},
         'user':{'mid':999,'nickname':'回复者'},'reply_time':now}
    wrong_target={**row,'item':{**row['item'],'target_id':12,'source_id':56}}
    wrong_root={**row,'item':{**row['item'],'root_id':999,'source_id':57}}
    self_reply={**row,'user':{'mid':123,'nickname':'自己'}}
    async def read(stage,operation):
        operation.close()
        return {'items':[row,wrong_target,wrong_root,self_reply],'cursor':{'is_end':True}}
    engine.platform._read_stage=read
    own={'11':{'bvid':'BV13pCjYLEMM','root_rpid':9,'root_text':'自己的根评论','text':'自己的父评论'}}
    replies=await engine.platform.incoming_replies(own,now-10)
    assert len(replies)==1
    assert (replies[0].root_rpid,replies[0].source_rpid,replies[0].target_rpid)==(9,55,11)
    assert replies[0].sender_uid=='999'
    await engine.platform.close()


async def test_received_reply_recovers_after_model_failure_without_duplicate_draft(engine):
    engine.platform.uid='123'
    now=time.time()
    metadata={'aid':1,'bvid':'BV1xx411c7mD'}
    engine.store.execute('INSERT INTO videos VALUES(?,?,?,?,?,?,?,?,?)',('v',metadata['bvid'],1,dump(metadata),'{}','{}','understood','',now))
    engine.store.execute('INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('o','d','succeeded','123',engine.day(),'v','comment','','','','own','11',None,now,now))
    payload={'aid':1,'event_id':'123:55','sender_uid':'999','text':'新问题','source_rpid':55,'target_rpid':11,'root_rpid':11}
    engine.store.execute('INSERT INTO reply_events VALUES(?,?,?,?)',('123:55',dump(payload),'received',now))
    async def checked():
        return '123'
    async def empty(*args):
        return []
    drafted=[]
    async def draft(video,incoming):
        drafted.append(incoming['event_id'])
        return {'drafted':False,'reason':'没有新增信息'}
    engine.platform.check_account=checked
    engine.platform.incoming_replies=empty
    engine.interaction.create_draft=draft
    await engine.interaction.replies()
    await engine.interaction.replies()
    assert drafted==['123:55']
    assert engine.store.rows('SELECT state FROM reply_events')[0]['state']=='processed'
    await engine.platform.close()

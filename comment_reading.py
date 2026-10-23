"""只读评论的独立经历，不下载视频，不冒充观看记录，不触发视频评论发布。"""
import hashlib
import json
import time

from .schemas import CommentUnderstanding, DiscoveryChoice, Selection
from .security import guard_unobserved_senses
from .storage import dump, uid


class CommentReading:
    def __init__(self,engine):
        self.engine,self.store=engine,engine.store

    async def read(self,payload):
        engine,cfg=self.engine,self.engine.config()
        query=payload.get('query','')
        if not query:
            choice=await engine.host.generate('discover',{'purpose':'只阅读评论，不观看视频','allowed_topics':cfg.allowed_topics,
                'excluded_topics':cfg.excluded_topics,'interests':await self.store.call('rows','SELECT * FROM interests ORDER BY weight DESC LIMIT 15')},DiscoveryChoice)
            query=choice.query
        engine.validate_query(query)
        candidates=await engine.platform.discover(query)
        for candidate in candidates[:4]:
            engine.check()
            prior=await self.store.call('rows','SELECT id,state FROM comment_reads WHERE day=? AND bvid=?',(engine.day(),candidate['bvid']))
            if prior and prior[0]['state']!='failed':continue
            choice=await engine.host.generate('select',{'candidate':candidate,'activity':'只读评论','allowed_topics':cfg.allowed_topics,
                'excluded_topics':cfg.excluded_topics},Selection)
            if not choice.browse:continue
            engine.validate_scope(candidate,choice.topic)
            metadata=await engine.platform.metadata(candidate['bvid'])
            engine.validate_scope(metadata,choice.topic)
            if prior:
                record_id=prior[0]['id']
                await self.store.call('execute',"UPDATE comment_reads SET state='reading',reason='重试评论阅读，未观看视频' WHERE id=?",(record_id,))
            else:
                await self.store.call('reserve',engine.day(),{'comment_sessions':(1,cfg.max_comment_sessions)})
                record_id=uid()
                await self.store.call('execute','INSERT INTO comment_reads VALUES(?,?,?,?,?,?,?,?,?)',
                    (record_id,engine.day(),metadata['bvid'],dump(metadata),'[]','{}','reading','只阅读评论，未观看视频',time.time()))
            try:
                comments=await engine.platform.comment_context(metadata['aid'],limit=cfg.max_comments_read)
                await self.store.call('execute','UPDATE comment_reads SET comments=? WHERE id=?',(dump(comments),record_id))
                if not comments:
                    await self.store.call('execute',"UPDATE comment_reads SET state='empty',reason='没有可访问评论；未观看视频' WHERE id=?",(record_id,))
                    return {'comment_read_id':record_id,'empty':True,'watched':False}
                evidence={r['rpid']:r['text'] for r in comments}
                analysis=await engine.host.generate('read_comments',{'metadata':metadata,'comments':comments,
                    'coverage':'只取得本页评论；没有字幕、音频或画面，未观看视频'},CommentUnderstanding)
                if any(point.rpid not in evidence or point.quote not in evidence[point.rpid] for point in analysis.points):
                    raise ValueError('评论理解引用不在本次评论证据中')
                guard_unobserved_senses(analysis.feeling,{'types':['comments']})
                result={**analysis.model_dump(),'coverage':'仅阅读评论区抽样，未观看视频','topic':choice.topic,
                        'comment_count':len(comments),'watched':False,'suitable_for_video_comment':False}
                engine.check()
                await self.store.call('execute',"UPDATE comment_reads SET state='understood',analysis=? WHERE id=?",(dump(result),record_id))
                if cfg.memory_enabled:
                    text='只读评论（未观看视频；网友观点不是视频事实）：'+analysis.summary
                    source=metadata['url']+'#reply'+comments[0]['rpid']
                    now=time.time()
                    await self.store.call('execute','INSERT OR IGNORE INTO memories(id,video_id,text,fingerprint,importance,topics,source,status,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (uid(),'comments:'+record_id,text,hashlib.sha256((record_id+text).encode()).hexdigest(),.4,dump([choice.topic]),source,'active',now,now))
                return {'comment_read_id':record_id,'watched':False,'source':metadata['url'],'analysis':result}
            except BaseException as exc:
                import asyncio
                await asyncio.shield(self.store.call('execute',"UPDATE comment_reads SET state='failed',reason=? WHERE id=?",(type(exc).__name__+'：评论阅读未完成',record_id)))
                raise
        return {'skipped':True,'watched':False,'reason':'暂无未读且感兴趣的评论区'}

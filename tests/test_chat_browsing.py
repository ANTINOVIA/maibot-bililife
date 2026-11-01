import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bililife.chat_browsing import ChatBrowsing
from bililife.configuration import Settings
from bililife.schemas import DiscoveryChoice, MemoryProposal, Selection


def setup(engine):
    engine.settings = Settings(video_share_enabled=True, video_share_stream_id='owner',
        comments_enabled=True, adaptive_schedule_enabled=True, allowed_topics=['科学', '技术'])
    engine.activity_allowed = lambda: True
    engine.life_deadline = lambda start=None: time.time() + 1800
    engine.account_status = {'state': 'authenticated'}
    engine.ctx = SimpleNamespace(
        chat=SimpleNamespace(get_all_streams=AsyncMock(return_value=[{'stream_id': 'owner', 'platform': 'qq', 'user_id': 'owner-id'}])),
        send=SimpleNamespace(custom=AsyncMock(return_value={'sent': True, 'message_id': 'real-card'}), text=AsyncMock()),
        maisaka=SimpleNamespace(proactive=SimpleNamespace(trigger=AsyncMock(return_value={'success': True, 'queued': True, 'task_id': 'host-task'}))))
    metadata = {'bvid': 'BV1xx411c7mD', 'page': 1, 'title': '自制机器人', 'description': '技术', 'author': 'UP',
        'duration': 60, 'url': 'https://www.bilibili.com/video/BV1xx411c7mD?p=1', 'cover_url': 'https://i0.hdslb.com/test.jpg'}
    engine.platform.metadata = AsyncMock(return_value=metadata)
    engine.platform.discover = AsyncMock(return_value=[metadata])
    analysis = {'summary': '展示了机器人转向结构', 'details': [], 'feeling': '对转向机构好奇', 'questions': [], 'uncertainty': '仅抽帧'}
    engine.understanding.analyze = AsyncMock(return_value=({'types': ['keyframes'], 'coverage': '3 张抽帧，未完整观看'}, analysis))
    async def generate(stage, *args, **kwargs):
        if stage == 'discover': return DiscoveryChoice(query='技术 机器人', reason='兴趣')
        if stage == 'select': return Selection(browse=True, topic='技术', reason='符合范围')
        if stage == 'memory': return MemoryProposal(text='从抽帧看到机器人的转向结构', importance=.5, topics=['技术'])
        raise AssertionError(stage)
    engine.host.generate = generate
    return metadata


async def drain(engine):
    for _ in range(10):
        task = engine.store.claim(engine.owner, False, True)
        if not task:
            break
        await engine.run(task)
        await engine.chat_browsing.tick()


@pytest.mark.parametrize('mode,query', [('browse', ''), ('search', '机器人'), ('watch', 'BV1xx411c7mD')])
async def test_real_task_chain_finishes_and_sends_only_when_requested(engine, mode, query):
    setup(engine)
    created = await engine.chat_browsing.start('owner', mode, query, True)
    before = await engine.chat_browsing.status('owner', created['request_id'])
    assert before['state'] == 'queued' and not before['result'].get('video')
    engine.ctx.send.custom.assert_not_awaited()
    await drain(engine)
    final = await engine.chat_browsing.status('owner', created['request_id'])
    assert final['state'] == 'completed', final
    assert final['result']['video']['cached'] is False
    assert final['result']['sharing']['sent'] is True
    assert final['notification_state'] == 'queued'
    engine.ctx.send.custom.assert_awaited_once()
    engine.ctx.maisaka.proactive.trigger.assert_awaited_once()
    engine.platform.metadata.assert_awaited_once()
    engine.understanding.analyze.assert_awaited_once()
    assert final['result']['video']['summary'] == '展示了机器人转向结构'
    for task in engine.store.rows('SELECT * FROM tasks'):
        payload = json.loads(task['payload'])
        assert payload['request_id'] == created['request_id'] and payload['stream_id'] == 'owner'
        assert task['type'] not in ('draft', 'adapt', 'publish')
    await engine.chat_browsing.tick()
    engine.ctx.send.custom.assert_awaited_once()
    engine.ctx.maisaka.proactive.trigger.assert_awaited_once()
    await engine.platform.close()


async def test_long_first_search_result_is_skipped_before_understanding(engine):
    metadata = setup(engine)
    engine.platform.discover.return_value = [{**metadata, 'bvid': 'BV1S9xT6zE4e', 'duration': 99999}, metadata]
    await engine.chat_browsing.start('owner', 'search', '机器人', True)
    await drain(engine)
    status = await engine.chat_browsing.status('owner')
    assert status['state'] == 'completed' and status['result']['sharing']['sent']
    engine.understanding.analyze.assert_awaited_once()
    assert engine.platform.metadata.call_args.args[0] == metadata['bvid']
    await engine.platform.close()


async def test_sharing_intent_can_be_added_to_same_running_request(engine):
    setup(engine)
    first = await engine.chat_browsing.start('owner', 'search', '机器人', False)
    updated = await engine.chat_browsing.start('owner', 'search', '机器人', True)
    assert updated['duplicate'] and updated['request_id'] == first['request_id'] and updated['share_after']
    await drain(engine)
    assert (await engine.chat_browsing.status('owner'))['result']['sharing']['sent']
    engine.ctx.send.custom.assert_awaited_once()
    await engine.platform.close()


async def test_no_share_without_intent_and_progress_does_not_read_old_videos(engine):
    setup(engine)
    created = await engine.chat_browsing.start('owner', 'browse')
    await drain(engine)
    status = await engine.chat_browsing.status('owner')
    assert status['request_id'] == created['request_id']
    assert status['result']['sharing']['state'] == 'not_requested'
    engine.ctx.send.custom.assert_not_awaited()
    with pytest.raises(ValueError):
        await engine.chat_browsing.status('stranger', created['request_id'])
    assert not (await engine.chat_browsing.status('owner', 'no-such-request'))['success']
    await engine.platform.close()


async def test_duplicate_concurrent_instances_create_one_task(engine):
    setup(engine)
    a, b = await asyncio.gather(engine.chat_browsing.start('owner'), ChatBrowsing(engine).start('owner'))
    assert a['request_id'] == b['request_id']
    assert len(engine.store.rows('SELECT * FROM tasks')) == 1
    with pytest.raises(ValueError, match='已有浏览任务'):
        await engine.chat_browsing.start('owner', 'search', '机器人')
    await engine.platform.close()


async def test_search_no_result_does_not_reuse_previous_experience(engine):
    setup(engine)
    await engine.chat_browsing.start('owner', 'watch', 'BV1xx411c7mD')
    await drain(engine)
    created = await engine.chat_browsing.start('owner', 'search', '机器人')
    await drain(engine)
    status = await engine.chat_browsing.status('owner', created['request_id'])
    assert status['state'] == 'failed' and not status['result'].get('video')
    assert '未使用旧经历' in status['error']
    await engine.platform.close()


async def test_known_video_is_explicitly_cached(engine):
    setup(engine)
    await engine.chat_browsing.start('owner', 'watch', 'BV1xx411c7mD')
    await drain(engine)
    await engine.chat_browsing.start('owner', 'watch', 'BV1xx411c7mD')
    await drain(engine)
    assert (await engine.chat_browsing.status('owner'))['result']['video']['cached'] is True
    engine.understanding.analyze.assert_awaited_once()
    await engine.platform.close()


async def test_watch_failure_is_reported_and_not_shared(engine):
    setup(engine)
    engine.understanding.analyze.side_effect = ValueError('没有可理解的内容')
    await engine.chat_browsing.start('owner', 'watch', 'BV1xx411c7mD', True)
    await drain(engine)
    result = await engine.chat_browsing.status('owner')
    assert result['state'] == 'failed' and '没有可理解的内容' in result['error']
    engine.ctx.send.custom.assert_not_awaited()
    engine.ctx.maisaka.proactive.trigger.assert_awaited_once()
    await engine.platform.close()


async def test_callback_timeout_does_not_repeat_and_restart_does_not_resend(engine):
    setup(engine)
    engine.ctx.maisaka.proactive.trigger.side_effect = TimeoutError()
    await engine.chat_browsing.start('owner', 'watch', 'BV1xx411c7mD', True)
    await drain(engine)
    await ChatBrowsing(engine).recover()
    await engine.chat_browsing.tick()
    result = await engine.chat_browsing.status('owner')
    assert result['notification_state'] == 'unknown'
    assert result['result']['sharing']['sent'] is True
    engine.ctx.send.custom.assert_awaited_once()
    engine.ctx.maisaka.proactive.trigger.assert_awaited_once()
    await engine.platform.close()


async def test_metadata_only_is_not_understanding(engine):
    setup(engine)
    engine.understanding.analyze.return_value = ({'types': ['metadata'], 'coverage': '只有标题'}, {'summary': '标题'})
    await engine.chat_browsing.start('owner', 'watch', 'BV1xx411c7mD')
    await drain(engine)
    assert (await engine.chat_browsing.status('owner'))['state'] == 'failed'
    await engine.platform.close()


async def test_paused_resting_excluded_and_disabled_share_fail_before_queue(engine):
    setup(engine)
    engine.paused = True
    with pytest.raises(ValueError, match='暂停'):
        await engine.chat_browsing.start('owner')
    engine.paused = False
    engine.activity_allowed = lambda: False
    with pytest.raises(ValueError, match='非活跃'):
        await engine.chat_browsing.start('owner')
    engine.activity_allowed = lambda: True
    engine.settings.video_share_enabled = False
    with pytest.raises(ValueError, match='分享已关闭'):
        await engine.chat_browsing.start('owner', share_after=True)
    assert engine.store.rows('SELECT * FROM tasks') == []
    await engine.platform.close()


async def test_short_link_only_resolves_once_to_valid_bilibili_video(engine):
    setup(engine)
    engine.platform.public_http.head = AsyncMock(return_value=SimpleNamespace(status_code=302,
        headers={'location': 'https://www.bilibili.com/video/BV1xx411c7mD?p=2'}))
    request = await engine.chat_browsing.start('owner', 'watch', 'https://b23.tv/Abc123')
    assert (await engine.chat_browsing.status('owner', request['request_id']))['query'].endswith('?p=2')
    engine.platform.public_http.head.assert_awaited_once()
    engine.platform.public_http.head.return_value.headers['location'] = 'https://127.0.0.1/private'
    with pytest.raises(ValueError):
        await engine.chat_browsing.start('owner', 'watch', 'https://b23.tv/Def456')
    assert len(engine.store.rows('SELECT * FROM tasks')) == 1
    await engine.platform.close()


async def test_failed_local_memory_does_not_erase_successful_content_understanding(engine):
    setup(engine)
    engine.memory.extract = AsyncMock(side_effect=ValueError('本地经历整理失败'))
    await engine.chat_browsing.start('owner', 'watch', 'BV1xx411c7mD', True)
    await drain(engine)
    status = await engine.chat_browsing.status('owner')
    assert status['state'] == 'completed' and status['result']['video']['summary']
    assert status['result']['local_experience']['state'] == 'failed'
    assert status['result']['sharing']['sent'] is False
    engine.ctx.send.custom.assert_not_awaited()
    await engine.platform.close()


async def test_manual_requests_respect_storage_slot_policy_when_available(engine):
    setup(engine)
    engine.store.slot_conflicts = lambda db, day, start, end: True
    with pytest.raises(ValueError, match='已有其他活动'):
        await engine.chat_browsing.start('owner')
    assert not engine.store.rows('SELECT * FROM chat_requests')
    assert not engine.store.rows('SELECT * FROM tasks')
    await engine.platform.close()

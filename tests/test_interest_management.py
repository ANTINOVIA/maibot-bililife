import asyncio
from types import SimpleNamespace

import pytest

from bililife.configuration import Settings
from bililife.config_io import parse_settings_document
from bililife.schemas import Evolution
from bililife.storage import dump
from bililife.task_executor import TaskExecutor
from bililife.webui_service import WebUIService


@pytest.fixture
async def web(engine):
    yield WebUIService(engine)
    await engine.platform.close()


async def test_edit_weight_and_freeze_without_mandatory_reason(web, engine):
    first = await web.interest_save(dump({'topic': '科学', 'weight': .6}))
    updated = await web.interest_save(dump({'original_topic': '科学', 'topic': '科学', 'weight': .9, 'frozen': True, 'reason': ''}))
    assert engine.store.rows('SELECT * FROM interests') == [{'topic': '科学', 'weight': .9, 'frozen': 1}]
    assert updated['revision'] > first['revision'] and not updated['renamed']
    assert engine.store.rows('SELECT old,new FROM persona_changes WHERE id=?', (updated['change_id'],)) == [{'old': .6, 'new': .9}]


async def test_rename_replaces_original_and_blocks_old_history(web, engine):
    old = await web.interest_save(dump({'topic': '科学', 'weight': .7}))
    result = await web.interest_save(dump({'original_topic': '科学', 'topic': '科学实验', 'weight': .8, 'frozen': True}))
    assert result['renamed']
    assert engine.store.rows('SELECT * FROM interests') == [{'topic': '科学实验', 'weight': .8, 'frozen': 1}]
    assert '科学' not in engine.config().allowed_topics and '科学实验' in engine.config().allowed_topics
    saved = parse_settings_document((engine.data / 'settings.toml').read_text(encoding='utf-8'))
    assert saved['allowed_topics'] == engine.config().allowed_topics
    with pytest.raises(ValueError, match='删除或更名'):
        await web.interest_action(old['change_id'], 'rollback')
    await web.interest_action(result['change_id'], 'rollback')
    assert engine.store.rows('SELECT weight FROM interests WHERE topic=?', ('科学实验',))[0]['weight'] == .7


async def test_conflicting_or_stale_rename_does_not_change_settings(web, engine):
    await web.interest_save(dump({'topic': '科学', 'weight': .6}))
    await web.interest_save(dump({'topic': '天文', 'weight': .8}))
    settings = engine.config().model_dump()
    content = (engine.data / 'settings.toml').read_bytes()
    with pytest.raises(ValueError, match='同名'):
        await web.interest_save(dump({'original_topic': '科学', 'topic': '天文', 'weight': .4}))
    with pytest.raises(ValueError, match='不存在'):
        await web.interest_save(dump({'original_topic': '不存在', 'topic': '新主题', 'weight': .4}))
    assert engine.config().model_dump() == settings
    assert (engine.data / 'settings.toml').read_bytes() == content
    assert len(engine.store.rows('SELECT * FROM persona_changes')) == 2


async def test_delete_persists_across_restart_and_keeps_history(web, engine):
    old = await web.interest_save(dump({'topic': '科学', 'weight': .6}))
    result = await web.interest_action('科学', 'delete')
    assert result['ok'] and not result['auto_publish_disabled']
    assert not engine.store.rows('SELECT * FROM interests WHERE topic=?', ('科学',))
    assert '科学' not in engine.config().allowed_topics
    assert engine.store.rows('SELECT * FROM persona_changes WHERE id=?', (old['change_id'],))
    restarted = TaskExecutor(SimpleNamespace(), engine.data, engine.runtime)
    async def load():
        pass
    async def idle():
        await asyncio.Future()
    restarted.platform.load = load
    restarted.loop = idle
    try:
        await restarted.start()
        assert '科学' not in restarted.config().allowed_topics
        assert not restarted.store.rows('SELECT * FROM interests WHERE topic=?', ('科学',))
    finally:
        await restarted.stop()


async def test_delete_last_scope_turns_off_only_automatic_publishing(web, engine):
    await web.save_settings(dump(Settings(allowed_topics=['科学'], auto_publish_comments=True, auto_publish_replies=True).model_dump()))
    await web.interest_save(dump({'topic': '科学', 'weight': .6}))
    result = await web.interest_action('科学', 'delete')
    assert result['auto_publish_disabled'] and not engine.config().allowed_topics
    assert not engine.config().auto_publish_comments and not engine.config().auto_publish_replies
    assert engine.config().comments_enabled and engine.config().replies_enabled
    saved = parse_settings_document((engine.data / 'settings.toml').read_text(encoding='utf-8'))
    assert not saved['auto_publish_comments'] and not saved['auto_publish_replies']


async def test_readding_deleted_interest_does_not_reenable_old_versions(web, engine):
    old = await web.interest_save(dump({'topic': '科学', 'weight': .9}))
    await web.interest_action('科学', 'delete')
    current = await web.interest_save(dump({'topic': '科学', 'weight': .3}))
    await web.interest_action(current['change_id'], 'rollback')
    with pytest.raises(ValueError, match='删除或更名'):
        await web.interest_action(old['change_id'], 'rollback')
    assert engine.store.rows('SELECT weight FROM interests WHERE topic=?', ('科学',))[0]['weight'] == .5


async def test_stale_evolution_cannot_recreate_deleted_topic(web, engine):
    await web.interest_save(dump({'topic': '科学', 'weight': .6}))
    stale = engine.config().model_copy(deep=True)
    await web.interest_action('科学', 'delete')
    # Exercise the commit-time check even if an in-flight caller holds old settings.
    engine.settings = stale
    async def propose(*args):
        return Evolution(changes=[{'topic': '科学', 'delta': .02, 'reason': '三个来源'}])
    engine.host.generate = propose
    result = await engine.evolution.evolve(engine.day(), [{'bvid': str(i), 'id': str(i)} for i in range(3)])
    assert not result['changes']
    assert not engine.store.rows('SELECT * FROM interests WHERE topic=?', ('科学',))


async def test_failed_persistence_rolls_back_delete(web, engine, monkeypatch):
    await web.interest_save(dump({'topic': '科学', 'weight': .6}))
    before = engine.config().model_dump()
    def fail(*args):
        raise OSError('disk write failed')
    monkeypatch.setattr('bililife.webui_service.write_settings_file', fail)
    with pytest.raises(OSError):
        await web.interest_action('科学', 'delete')
    assert engine.store.rows('SELECT weight FROM interests WHERE topic=?', ('科学',))[0]['weight'] == .6
    assert engine.config().model_dump() == before
    assert not engine.store.get('interest_retired_before:科学')


@pytest.mark.parametrize('action', ['freeze', 'unfreeze', 'delete'])
async def test_nonexistent_interest_reports_error(web, action):
    with pytest.raises(ValueError, match='不存在'):
        await web.interest_action('不存在', action)

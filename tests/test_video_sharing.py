import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bililife.configuration import Settings
from bililife.video_sharing import VideoSharing, video_card


def seed(engine, bvid="BV1xx411c7mD", cover=True):
    video_id = bvid + ":p1"
    metadata = {"bvid": bvid, "page": 1, "title": "科学视频", "author": "UP", "duration": 60,
        "url": "https://www.bilibili.com/video/" + bvid,
        "cover_url": "http://i0.hdslb.com/test.jpg" if cover else ""}
    engine.store.execute("INSERT INTO videos VALUES(?,?,?,?,?,?,?,?,?)", (video_id, bvid, 1, json.dumps(metadata),
        '{"coverage":"部分字幕/抽帧"}', '{"summary":"作者的观点","feeling":"想了解更多"}', "understood", "", time.time()))
    engine.store.execute("INSERT INTO memories(id,video_id,text,fingerprint,importance,topics,source,status,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (video_id, video_id, "真实见闻", video_id, .5, "[]", metadata["url"], "active", time.time(), time.time()))
    return video_id, metadata


def setup(engine):
    engine.settings = Settings(video_share_enabled=True, video_share_stream_id="owner")
    engine.activity_allowed = lambda: True
    engine.ctx = SimpleNamespace(chat=SimpleNamespace(get_all_streams=AsyncMock(return_value=[{"stream_id": "owner", "platform": "qq"}])),
        send=SimpleNamespace(custom=AsyncMock(return_value={"sent": True, "message_id": "qq-card"}),
            text=AsyncMock(return_value={"sent": True, "message_id": "qq-text"})))


async def test_card_receipt_duplicate_and_restart(engine):
    setup(engine)
    video_id, _ = seed(engine)
    result = await engine.sharing.share(video_id, "owner")
    assert result["sent"] and result["message_id"] == "qq-card"
    args, kwargs = engine.ctx.send.custom.call_args
    assert args[0] == "qq_ark" and args[2] == "owner"
    kv = {x["key"]: x["value"] for x in args[1]["kv"]}
    assert kv["#METACOVER#"] == "https://i0.hdslb.com/test.jpg"
    assert kv["#METAURL#"].endswith("BV1xx411c7mD?p=1")
    assert kwargs["sync_to_maisaka_history"] and "BV1xx411c7mD" in kwargs["processed_plain_text"]
    assert (await VideoSharing(engine).share(video_id, "owner"))["duplicate"]
    engine.ctx.send.custom.assert_awaited_once()
    assert await engine.sharing.candidates("owner") == []
    await engine.platform.close()


@pytest.mark.parametrize("outcome", [False, "timeout", "exception"])
async def test_ambiguous_result_is_not_retried_or_downgraded(engine, outcome):
    setup(engine)
    video_id, _ = seed(engine)
    engine.ctx.send.custom.return_value = {"sent": False}
    if outcome != False:
        engine.ctx.send.custom.side_effect = TimeoutError() if outcome == "timeout" else RuntimeError("network failed after acceptance")
    assert (await engine.sharing.share(video_id))["state"] == "unknown"
    assert (await VideoSharing(engine).share(video_id))["duplicate"]
    assert engine.ctx.send.custom.await_count == 1
    engine.ctx.send.text.assert_not_awaited()
    await engine.platform.close()


async def test_concurrent_instances_claim_one_attempt(engine):
    setup(engine)
    video_id, _ = seed(engine)
    result = await asyncio.gather(engine.sharing.share(video_id), VideoSharing(engine).share(video_id))
    assert sum(bool(x.get("duplicate")) for x in result) == 1
    engine.ctx.send.custom.assert_awaited_once()
    await engine.platform.close()


@pytest.mark.parametrize("condition", ["disabled", "wrong_caller", "paused", "resting", "forgotten", "unread", "wrong_platform", "unknown_target", "quota"])
async def test_checks_before_any_outbound_attempt(engine, condition):
    setup(engine)
    video_id, _ = seed(engine)
    if condition == "disabled": engine.settings.video_share_enabled = False
    if condition == "paused": engine.paused = True
    if condition == "resting": engine.activity_allowed = lambda: False
    if condition == "forgotten": engine.store.execute("UPDATE memories SET status='forgotten'")
    if condition == "unread": engine.store.execute("UPDATE videos SET state='reading'")
    if condition == "wrong_platform": engine.ctx.chat.get_all_streams.return_value[0]["platform"] = "internal"
    if condition == "unknown_target": engine.ctx.chat.get_all_streams.return_value = []
    if condition == "quota": engine.settings.video_share_daily_limit = 0
    with pytest.raises(ValueError):
        await engine.sharing.share(video_id, "other" if condition == "wrong_caller" else "owner")
    engine.ctx.send.custom.assert_not_awaited()
    engine.ctx.send.text.assert_not_awaited()
    await engine.platform.close()


async def test_legacy_cover_refresh_and_disabled_during_await(engine):
    setup(engine)
    video_id, _ = seed(engine, cover=False)
    async def refresh(_):
        engine.settings = engine.settings.model_copy(update={"video_share_enabled": False})
        return {"cover_url": "https://i0.hdslb.com/cover.jpg"}
    engine.platform.metadata = refresh
    with pytest.raises(ValueError):
        await engine.sharing.share(video_id)
    engine.ctx.send.custom.assert_not_awaited()
    await engine.platform.close()


async def test_interrupted_attempt_recovers_as_unknown(engine):
    setup(engine)
    video_id, _ = seed(engine)
    engine.sharing.claim(video_id, engine.config())
    await engine.sharing.recover()
    assert (await engine.sharing.share(video_id))["state"] == "unknown"
    engine.ctx.send.custom.assert_not_awaited()
    await engine.platform.close()


async def test_explicit_text_mode_and_attempt_cooldown(engine):
    setup(engine)
    engine.settings.video_share_format = "text"
    video_id, _ = seed(engine)
    assert (await engine.sharing.share(video_id))["sent"]
    other_id, _ = seed(engine, bvid="BV1S9xT6zE4e")
    with pytest.raises(ValueError, match="冷却"):
        await engine.sharing.share(other_id)
    engine.ctx.send.custom.assert_not_awaited()
    assert len(await engine.sharing.candidates("owner")) == 1
    await engine.platform.close()


def test_card_rejects_untrusted_or_empty_cover():
    for cover in ["", "https://127.0.0.1/a", "https://example.com/a", "https://i0.hdslb.com:8080/a"]:
        with pytest.raises(ValueError):
            video_card({"url": "https://www.bilibili.com/video/BV1xx411c7mD", "title": "title", "cover_url": cover})

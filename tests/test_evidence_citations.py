import copy

import pytest

from bililife.configuration import Settings
from bililife.media_processor import MediaProcessor
from bililife.schemas import Understanding
from bililife.video_understanding import evidence_references, resolve_citation
from test_invariants import seed_video


@pytest.mark.parametrize('kind,label', [('video', 'video_segment_1@0-60s'),
                                      ('audio', 'audio_segment_1@0-60s'),
                                      ('frame', 'frame_1@20.0s')])
async def test_media_citation_repair_has_exact_labels(engine, monkeypatch, kind, label):
    metadata = seed_video(engine)
    async def subtitles(_):
        return [], '没有字幕'
    async def collect(self, metadata, evidence, video_id):
        evidence['types'].append('keyframes' if kind == 'frame' else 'direct_' + kind)
        evidence['frames' if kind == 'frame' else 'media_segments'] = [{'label': label}]
        return (['image'], None) if kind == 'frame' else ([], {'kind': kind})
    monkeypatch.setattr(MediaProcessor, 'collect', collect)
    engine.platform.subtitles = subtitles
    calls = []
    async def generate(stage, external, *args, **kwargs):
        calls.append(copy.deepcopy(external))
        return Understanding(summary='片段观察', details=[dict(claim_type='checkable_detail',
            text='片段内的细节', evidence_quote='概括不是引用' if len(calls) == 1 else '“' + label + '”')],
            feeling='好奇', questions=[], uncertainty='仅抽样', visual_required=False,
            suitable_for_comment=False, reason='有限证据')
    engine.host.generate = generate
    _, result = await engine.understanding.analyze(metadata, 'v1')
    assert len(calls) == 2
    assert calls[0]['citation_guide']['media_labels'] == [label]
    assert calls[1]['validation_feedback']['invalid_details'][0]['rejected_quote'] == '概括不是引用'
    assert result['details'][0]['evidence_quote'] == label


@pytest.mark.parametrize('source_id,expected', [('subtitle_1', '真实字幕'),
    ('metadata:title', '标题'), ('video_segment_1@0-90s', 'video_segment_1@0-90s')])
def test_source_ids_resolve_to_actual_source(source_id, expected):
    refs = evidence_references({'title': '标题'}, {'segments': [{'text': '真实字幕'}],
        'media_segments': [{'label': 'video_segment_1@0-90s'}]})
    assert resolve_citation('', source_id, refs) == expected


def test_invalid_id_cannot_be_masked_by_valid_quote_or_another_source():
    refs = evidence_references({'title': '标题'}, {'segments': [{'text': '真实字幕'}]})
    assert resolve_citation('真实字幕', 'subtitle_999', refs) is None
    assert resolve_citation('真实字幕', 'metadata:title', refs) is None
    assert resolve_citation('真实字幕的概括', '', refs) is None
    assert resolve_citation('', '', refs) is None


def test_adjacent_subtitle_quote_preserves_original_line_breaks():
    refs = evidence_references({}, {'segments': [{'text': 'AI 正在', 'from': 0, 'to': 2},
        {'text': '改变工作方式', 'from': 2, 'to': 4}]})
    assert resolve_citation('AI正在改变工作方式', '', refs) == 'AI 正在\n改变工作方式'
    assert resolve_citation('AI正在取代全部工作', '', refs) is None
    assert resolve_citation('AI正在改变工作方式', 'subtitle_1', refs) is None


def test_separated_subtitle_samples_cannot_be_spliced_into_a_quote():
    refs = evidence_references({}, {'segments': [{'text': 'AI 正在', 'from': 0, 'to': 2},
        {'text': '改变工作方式', 'from': 300, 'to': 302}]})
    assert resolve_citation('AI正在改变工作方式', '', refs) is None


async def test_understanding_accepts_source_id_and_keeps_full_retry_context(engine):
    metadata = seed_video(engine)
    engine.settings = Settings(video_understanding_mode='subtitle', enable_asr=False)
    async def subtitles(_):
        return [{'text': '真实字幕', 'from': 0, 'to': 2}], '有限字幕'
    engine.platform.subtitles = subtitles
    calls = []
    async def generate(stage, external, *args, **kwargs):
        calls.append(copy.deepcopy(external))
        return Understanding(summary='作者介绍了真实字幕中的内容',
            details=[dict(claim_type='checkable_detail', text='字幕中的内容',
                          evidence_id='subtitle_999' if len(calls) == 1 else 'subtitle_1')],
            feeling='好奇', questions=[], uncertainty='部分字幕', visual_required=False,
            suitable_for_comment=True, reason='有来源')
    engine.host.generate = generate
    _, result = await engine.understanding.analyze(metadata, 'v1')
    assert len(calls) == 2 and result['details'][0]['evidence_quote'] == '真实字幕'
    assert result['details'][0]['evidence_id'] == 'subtitle_1'
    assert calls[0]['evidence']['segments'][0]['source_id'] == 'subtitle_1'
    assert calls[1]['validation_feedback']['previous_result']['summary'] == '作者介绍了真实字幕中的内容'
    audit = engine.store.rows("SELECT data FROM audit WHERE kind='evidence_validation_error'")
    assert 'subtitle_999' in audit[0]['data']


async def test_fabricated_quote_still_fails_after_two_attempts(engine):
    metadata = seed_video(engine)
    engine.settings = Settings(video_understanding_mode='subtitle', enable_asr=False)
    async def subtitles(_):
        return [{'text': '真实字幕', 'from': 0, 'to': 2}], '有限字幕'
    engine.platform.subtitles = subtitles
    calls = []
    async def generate(*args, **kwargs):
        calls.append(1)
        return Understanding(summary='摘要', details=[dict(claim_type='checkable_detail',
            text='推测', evidence_quote='video_segment_999@0-60s')], feeling='好奇', questions=[],
            uncertainty='有限资料', visual_required=False, suitable_for_comment=True, reason='错误')
    engine.host.generate = generate
    with pytest.raises(ValueError, match='两次未提供'):
        await engine.understanding.analyze(metadata, 'v1')
    assert len(calls) == 2

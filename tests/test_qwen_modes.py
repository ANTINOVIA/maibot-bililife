from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from bililife.configuration import Settings,migrate_settings
from bililife.config_io import parse_settings_document
from bililife.media_processor import MediaProcessor
from bililife.model_router import Provider,service_error
from bililife.model_diagnostics import check_model
from bililife.model_diagnostics import failure
from bililife.storage import dump
from test_model_providers import router


def entry(**changes):
    return dict(id='qwen_video',name='Qwen 视频',protocol='qwen_compatible',base_url='https://dashscope.aliyuncs.com/compatible-mode/v1',
                model='qwen3.8-flash',capabilities=['video'],api_key='unit-key',**changes)


async def test_qwen_compatible_video_format_and_thinking_flag(router):
    await router.save_provider(dump(entry()))
    await router.save_routes(dump({'text':'','vision':'','asr':'','audio':'disabled','video':'qwen_video'}))
    received=[]
    async def request(provider,suffix,**kwargs):
        received.append(kwargs['payload'])
        assert suffix=='/chat/completions'
        return {'choices':[{'message':{'content':'{"ok":true}'}}],'usage':{'total_tokens':7}}
    router.request=request
    messages=[{'role':'system','content':'rules'},{'role':'user','content':'evidence'}]
    await router.generate(messages,'video',256,{'kind':'video','mime':'video/mp4','bytes':b'clip'})
    body=received[0]
    assert body['enable_thinking'] is False and body['stream'] is False
    video=body['messages'][1]['content'][-1]
    assert video['type']=='video_url' and video['fps']==1.0
    assert video['video_url']['url'].startswith('data:video/mp4;base64,')
    assert 'max_frames' not in video and messages[1]['content']=='evidence'


async def test_dashscope_native_messages_usage_and_video_bounds(router):
    definition=entry();definition.update(protocol='dashscope',base_url='https://dashscope.aliyuncs.com/api/v1',video_fps=.5,video_max_frames=8)
    await router.save_provider(dump(definition))
    await router.save_routes(dump({'text':'','vision':'','asr':'','audio':'disabled','video':'qwen_video'}))
    async def request(provider,suffix,**kwargs):
        assert suffix=='/services/aigc/multimodal-generation/generation'
        body=kwargs['payload']
        assert 'messages' not in body and body['parameters']['enable_thinking'] is False
        assert body['input']['messages'][0]['content']==[{'text':'rules'}]
        part=body['input']['messages'][1]['content'][-1]
        assert part['video'].startswith('data:video/mp4;base64,')
        assert part['fps']==.5 and part['max_frames']==8
        return {'output':{'choices':[{'message':{'content':[{'text':'{"ok":true}'}]}}]},'usage':{'input_tokens':10,'output_tokens':2}}
    router.request=request
    result=await router.generate([{'role':'system','content':'rules'},{'role':'user','content':'evidence'}],'video',256,{'kind':'video','mime':'video/mp4','bytes':b'clip'})
    assert result['success'] and result['usage']['total_tokens']==12


async def test_host_qwen_native_only_converts_official_same_origin_path(router,tmp_path):
    host=tmp_path/'models.toml'
    host.write_text('''[[models]]
name="Qwen"
model_identifier="qwen3.8-flash"
api_provider="Cloud"
model_type="llm"
[[api_providers]]
name="Cloud"
base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
api_key="host-key-kept-backend"
''',encoding='utf-8')
    router.host_path=host
    await router.save_routes(dump({'routes':{'text':'','vision':'','asr':'','audio':'disabled','video':'host:Qwen'},'host_options':{'video':{'protocol':'dashscope'}}}))
    assert (await router.resolve('video'))['base_url']=='https://dashscope.aliyuncs.com/api/v1'
    assert 'host-key-kept-backend' not in dump(await router.snapshot())
    host.write_text(host.read_text(encoding='utf-8').replace('dashscope.aliyuncs.com','proxy.example.com'),encoding='utf-8')
    with pytest.raises(RuntimeError,match='安全转换'):await router.resolve('video')


def test_qwen_protocol_validation_and_sanitized_service_errors():
    data=entry();data.pop('api_key');data.update(protocol='dashscope')
    with pytest.raises(ValueError,match='/api/v1'):Provider.model_validate(data)
    data.update(base_url='https://dashscope.aliyuncs.com/api/v1',video_fps=99)
    with pytest.raises(ValueError):Provider.model_validate(data)
    error=service_error(400,{'error':{'code':'InvalidParameter','message':'parameter enable_thinking must be false for non-streaming sk-do-not-echo'}})
    code,message,status=failure(error)
    assert code=='InvalidParameter' and status==400
    assert '关闭思考' in message and 'do-not-echo' not in message
    short=service_error(400,{'error':{'code':'invalid_parameter_error','message':'The video file is too short.'}})
    assert '视频片段过短' in str(short)


async def test_video_diagnostic_has_enough_frames_and_duration(engine,monkeypatch):
    monkeypatch.setattr('bililife.model_diagnostics.shutil.which',lambda _: 'ffmpeg')
    async def process(*args,**kwargs):
        assert args[args.index('-t')+1]=='4'
        assert 'color=c=green:s=128x96:r=4' in args
        Path(args[-1]).write_bytes(b'local-synthetic-video')
        async def wait():return 0
        return SimpleNamespace(returncode=0,wait=wait)
    async def generate(messages,role,max_tokens,media):
        assert role=='video' and media['seconds']==4
        return {'success':True,'response':'{"ok":true}','model':'qwen-video','usage':{}}
    monkeypatch.setattr('bililife.model_diagnostics.asyncio.create_subprocess_exec',process)
    engine.host.router.state['routes']['video']='unit-video'
    engine.host.router.generate=generate
    before=engine.store.rows('SELECT * FROM budget')
    result=await check_model(engine,'video')
    assert result['state']=='succeeded' and engine.store.rows('SELECT * FROM budget')==before
    assert not list(engine.runtime.glob('diagnostic-*.mp4'))
    await engine.platform.close()


@pytest.mark.parametrize('old,mode',[({'enable_frames':True},'frames'),({'enable_video_model':True,'enable_frames':True},'video'),({'enable_audio_model':True},'audio'),({'enable_frames':False},'subtitle')])
def test_legacy_mode_migration_is_explicit(old,mode):
    cfg=Settings.model_validate(migrate_settings(old))
    assert cfg.video_understanding_mode==mode
    assert not any(k.startswith('enable_') and k!='enable_asr' for k in cfg.model_dump())
    document='[browse]\n'+'\n'.join(k+' = '+str(v).lower() for k,v in old.items())
    assert parse_settings_document(document,legacy=True)['video_understanding_mode']==mode


@pytest.mark.parametrize('mode',['frames','video','subtitle'])
async def test_media_path_is_selected_exclusively(engine,monkeypatch,mode):
    monkeypatch.setattr('bililife.media_processor.shutil.which',lambda _: 'ffmpeg')
    cfg=Settings(video_understanding_mode=mode)
    operations=[]
    async def streams(metadata):
        operations.append('streams');return {'video':'https://x.bilivideo.com/v','audio':''}
    async def download(*args):return b'local-media'
    async def resolve(role):
        operations.append(role);return {'video_has_audio':False}
    async def ffmpeg(args):
        operations.append(Path(args[-1]).suffix)
        Path(args[-1]).write_bytes(b'local-output')
    engine.platform.streams=streams
    engine.platform.download=download
    engine.host.router.resolve=resolve
    evidence={'types':['metadata'],'segments':[],'frames':[],'limitations':[],'coverage':'元数据浏览'}
    processor=MediaProcessor(engine.platform,engine.host,lambda:cfg,engine.runtime,ffmpeg)
    images,media=await processor.collect({'duration':100},evidence,'choose-path')
    if mode=='frames':assert len(images)==3 and media is None and 'video' not in operations and '.mp4' not in operations
    elif mode=='video':assert not images and media['kind']=='video' and '.jpg' not in operations
    else:assert not images and media is None and not operations
    assert evidence['understanding_mode']==mode
    await engine.platform.close()

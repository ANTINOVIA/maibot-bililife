from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from bililife.model_router import ModelRouter, endpoint
from bililife.storage import dump


@pytest.fixture
def router(store):
    async def models():
        return ['utils','vlm','voice']
    return ModelRouter(SimpleNamespace(llm=SimpleNamespace(get_available_models=models)),store,lambda:SimpleNamespace(model_timeout_seconds=10,text_model_task='utils',vision_model_task='vlm',asr_model_task='voice'))


def definition(**kwargs):
    return {'id':'custom1','name':'测试提供商','protocol':'openai_compatible','base_url':'https://api.example.com/v1','model':'test-model','capabilities':['text','vision','asr','audio','video'],'video_has_audio':True,**kwargs}


async def test_custom_keys_never_returned_and_endpoint_binding(router):
    await router.save_provider(dump(definition(api_key='unit-secret-do-not-export')))
    state=await router.snapshot()
    assert state['providers'][0]['key_configured'] is True
    assert 'unit-secret-do-not-export' not in dump(state)
    assert 'unit-secret-do-not-export' not in router.path.read_text(encoding='utf-8')
    with pytest.raises(ValueError,match='重新填写'):
        await router.save_provider(dump(definition(base_url='https://different.example.com/v1')))
    await router.save_provider(dump(definition(base_url='https://different.example.com/v1',api_key='second-unit-secret')))
    await router.save_routes(dump({'text':'custom1','vision':'','asr':'','audio':'disabled','video':'custom1'}))
    resolved=await router.resolve('video')
    assert resolved['api_key']=='second-unit-secret'
    assert resolved['base_url']=='https://different.example.com/v1'


async def test_host_catalog_and_live_credential_reference(router,tmp_path):
    path=tmp_path/'model_config.toml'
    path.write_text('''[[models]]
name="existing-video"
model_identifier="actual-media-model"
api_provider="Existing"
model_type="llm"
[[api_providers]]
name="Existing"
base_url="https://existing.example.com/v1"
api_key="host-secret-reference"
client_type="openai"
[model_task_config.vlm]
model_list=["existing-video"]
''',encoding='utf-8')
    router.host_path=path
    assert 'host-secret-reference' not in dump(await router.catalog())
    await router.save_provider(dump(definition(id='inherited',source='maibot',host_model_name='existing-video')))
    await router.save_routes(dump({'text':'','vision':'','asr':'','audio':'disabled','video':'inherited'}))
    assert not router.secrets
    assert (await router.resolve('video'))['api_key']=='host-secret-reference'
    path.write_text(path.read_text().replace('host-secret-reference','rotated-host-secret'))
    assert (await router.resolve('video'))['api_key']=='rotated-host-secret'
    assert 'rotated-host-secret' not in dump(await router.snapshot())


async def test_capability_routes_validate_and_referenced_provider_not_removed(router):
    await router.save_provider(dump(definition(capabilities=['text'],video_has_audio=False,api_key='fake-key')))
    with pytest.raises(ValueError,match='未声明'):
        await router.save_routes(dump({'text':'','vision':'','asr':'','audio':'disabled','video':'custom1'}))
    await router.save_routes(dump({'text':'custom1','vision':'','asr':'','audio':'disabled','video':'disabled'}))
    with pytest.raises(ValueError,match='仍被角色使用'):
        await router.remove_provider('custom1')


async def test_video_compatible_protocol_receives_real_bytes_without_remote_media_url(router):
    await router.save_provider(dump(definition(api_key='fake-key')))
    await router.save_routes(dump({'text':'','vision':'','asr':'','audio':'disabled','video':'custom1'}))
    requests=[]
    async def request(provider,suffix,**kwargs):
        requests.append((suffix,kwargs))
        return {'model':'returned-model','choices':[{'message':{'content':'{"summary":"test"}'}}],'usage':{'total_tokens':10}}
    router.request=request
    result=await router.generate([{'role':'system','content':'rules'},{'role':'user','content':'external data'}],'video',100,{'kind':'video','mime':'video/mp4','bytes':b'unit-video-bytes'})
    assert result['model']=='returned-model'
    assert requests[0][0]=='/chat/completions'
    video=requests[0][1]['payload']['messages'][1]['content'][1]
    assert video['type']=='video_url' and video['video_url']['url'].startswith('data:video/mp4;base64,')


async def test_media_retry_does_not_duplicate_input(router):
    await router.save_provider(dump(definition(api_key='fake-key')))
    await router.save_routes(dump({'text':'','vision':'','asr':'','audio':'custom1','video':'disabled'}))
    sizes=[]
    async def request(provider,suffix,**kwargs):
        sizes.append(len(kwargs['payload']['messages'][1]['content']))
        return {'choices':[{'message':{'content':'{}'}}]}
    router.request=request
    messages=[{'role':'system','content':'rules'},{'role':'user','content':[{'type':'text','text':'evidence'}]}]
    media={'kind':'audio','mime':'audio/wav','bytes':b'unit-wav'}
    await router.generate(messages,'audio',100,media)
    await router.generate(messages,'audio',100,media)
    assert sizes==[2,2]
    assert len(messages[1]['content'])==1


async def test_gemini_audio_native_payload(router):
    await router.save_provider(dump(definition(protocol='gemini',capabilities=['audio','video'],api_key='fake-key')))
    await router.save_routes(dump({'text':'','vision':'','asr':'','audio':'custom1','video':'disabled'}))
    async def request(provider,suffix,**kwargs):
        assert suffix=='/models/test-model:generateContent'
        part=kwargs['payload']['contents'][0]['parts'][-1]
        assert part['inline_data']['mime_type']=='audio/wav'
        return {'candidates':[{'content':{'parts':[{'text':'{"ok":true}'}]}}],'modelVersion':'returned-native-version','usageMetadata':{'totalTokenCount':20}}
    router.request=request
    result=await router.generate([{'role':'system','content':'rules'},{'role':'user','content':'external data'}],'audio',100,{'kind':'audio','mime':'audio/wav','bytes':b'unit-wav'})
    assert result['model']=='returned-native-version' and result['usage']['total_tokens']==20


def test_provider_endpoint_does_not_allow_metadata_or_credentials():
    for url in ('http://api.example.com/v1','https://169.254.169.254/v1','https://127.0.0.1/v1','https://user:pass@api.example.com/v1','https://api.example.com/v1?api_key=secret'):
        with pytest.raises(ValueError):
            endpoint(url)


async def test_choose_host_model_directly_uses_sdk_without_inherited_entry(router,tmp_path):
    path=tmp_path/'host.toml'
    path.write_text('''[[models]]
name="host-text"
model_identifier="real-text"
api_provider="Example"
model_type="llm"
[[api_providers]]
name="Example"
base_url="https://api.example.com/v1"
api_key="host-secret-should-stay-backend"
''',encoding='utf-8')
    router.host_path=path
    calls=[]
    async def generate(messages,**kwargs):
        calls.append(kwargs)
        return {'success':True,'response':'{}','model':'actual-returned-model'}
    router.ctx.llm.generate=generate
    await router.save_routes(dump({'text':'host:host-text','vision':'disabled','asr':'disabled','audio':'disabled','video':'disabled'}))
    response=await router.generate([{'role':'user','content':'input'}],'text',100)
    assert response['success'] and calls[0]['model_name']=='host-text'
    assert not router.state['providers'] and not router.secrets
    assert (await router.snapshot())['routes']['text']=='host:host-text'
    assert 'host-secret-should-stay-backend' not in dump(await router.snapshot())


async def test_custom_provider_can_be_saved_from_toml(router):
    await router.save_provider('''id = "asr_custom"
name = "语音转录"
base_url = "https://api.example.com/v1"
model = "real-asr"
capabilities = ["asr"]
api_key = "toml-unit-secret"
''')
    row=(await router.snapshot())['providers'][0]
    assert row['id']=='asr_custom' and row['key_configured']
    assert 'toml-unit-secret' not in dump(await router.snapshot())

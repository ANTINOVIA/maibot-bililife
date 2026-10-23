"""独立模型诊断：不经过生活预算、人格或内容分析流水线。"""
import asyncio
import base64
from io import BytesIO
import json
import re
import shutil
import ssl
import time
import wave

import aiohttp
from PIL import Image

from .security import redact
from .storage import dump, uid


class DiagnosticError(RuntimeError):
    def __init__(self, code, message, http_status=None):
        super().__init__(message)
        self.code,self.http_status=code,http_status


def failure(exc):
    if isinstance(exc,DiagnosticError):
        return exc.code,str(exc),exc.http_status
    if isinstance(exc,(asyncio.TimeoutError,TimeoutError)):
        return 'TIMEOUT','连接检查超时',None
    if isinstance(exc,(ssl.SSLError,aiohttp.ClientSSLError)):
        return 'TLS_ERROR','TLS 连接或证书校验失败',None
    if isinstance(exc,aiohttp.ClientConnectorError):
        return 'CONNECT_ERROR','无法建立连接，请检查域名和网络',None
    if isinstance(exc,(ValueError,json.JSONDecodeError)):
        return 'INVALID_RESPONSE','模型响应格式无效',None
    # 宿主只提供错误文字时仅提取明确的状态码，不猜测不存在的 HTTP 状态。
    match=re.search(r'HTTP\s*(\d{3})',str(exc),re.I)
    if match:return 'HTTP_'+match[1],'服务返回 HTTP '+match[1],int(match[1])
    return 'MODEL_ERROR','宿主或提供商调用失败：'+type(exc).__name__,None


async def check_model(engine,role):
    from .model_router import ROLES
    if role not in ROLES:raise ValueError('未知模型角色')
    router=engine.host.router
    state=await engine.store.call('get','model_provider_tests',{})
    revision=router.state.get('revision',0)
    entry={'state':'running','target':router.state['routes'][role],'task_id':engine.current['id'] if engine.current else '',
           'updated':time.time(),'budget_scope':'diagnostic','error_code':None,'latency_ms':None}
    state[role]=entry
    await engine.store.call('update_model_test',role,entry)
    started=time.monotonic()
    request_started=None
    cache=None
    usage={}
    actual_model=''
    try:
        if router.state['routes'][role]=='disabled':raise DiagnosticError('MODEL_DISABLED','此用途未选择模型')
        router.check,router.ctx=engine.check,engine.ctx
        messages=[{'role':'system','content':'Return a short JSON object only.'},{'role':'user','content':'Return {"ok":true}.'}]
        media=None
        if role=='vision':
            buffer=BytesIO()
            Image.new('RGB',(32,32),(30,180,80)).save(buffer,format='PNG')
            messages[1]['content']=[{'type':'text','text':'Return {"ok":true} if an image is provided.'},
                                    {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode()}}]
        if role in ('asr','audio'):
            buffer=BytesIO()
            with wave.open(buffer,'wb') as audio:
                audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000);audio.writeframes(b'\0'*16000)
            media={'kind':'audio','mime':'audio/wav','bytes':buffer.getvalue(),'seconds':0.5}
        if role=='video':
            if not shutil.which('ffmpeg'):raise DiagnosticError('LOCAL_FFMPEG_MISSING','视频连接检查需要 FFmpeg')
            cache=engine.runtime/('diagnostic-'+uid()+'.mp4')
            process=await asyncio.create_subprocess_exec(shutil.which('ffmpeg'),'-nostdin','-hide_banner','-loglevel','error',
                '-f','lavfi','-i','color=c=green:s=128x96:r=1','-t','1','-an','-c:v','libx264','-pix_fmt','yuv420p','-y',str(cache),
                stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
            try:
                await asyncio.wait_for(process.wait(),15)
                if process.returncode:raise DiagnosticError('LOCAL_MEDIA_ERROR','诊断视频生成失败')
            finally:
                if process.returncode is None:process.kill();await process.wait()
            media={'kind':'video','mime':'video/mp4','bytes':await asyncio.to_thread(cache.read_bytes),'seconds':1}
        engine.check()
        request_started=time.monotonic()
        timeout=min(engine.config().model_timeout_seconds,30)
        if role=='asr':
            response=await asyncio.wait_for(router.transcribe(media['bytes']),timeout)
        else:
            response=await asyncio.wait_for(router.generate(messages,role,256,media),timeout)
        latency=round((time.monotonic()-request_started)*1000)
        if not isinstance(response,dict):raise DiagnosticError('INVALID_RESPONSE','模型未返回有效响应对象')
        if response.get('success') is False:
            status=response.get('http_status')
            if type(status) is int and 400<=status<600:raise DiagnosticError('HTTP_'+str(status),'服务返回 HTTP '+str(status),status)
            http=re.search(r'HTTP\s*(\d{3})',str(response.get('error','')),re.I)
            if http:raise DiagnosticError('HTTP_'+http[1],'服务返回 HTTP '+http[1],int(http[1]))
            raw=response.get('error_code',response.get('code','SDK_ERROR'))
            code=str(raw) if re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',str(raw)) else 'SDK_ERROR'
            raise DiagnosticError(code,'宿主模型接口报告调用失败')
        text=response.get('text','') if role=='asr' else response.get('response',response.get('content',response.get('text','')))
        if not isinstance(text,str) or (role!='asr' and not text.strip()):raise DiagnosticError('EMPTY_RESPONSE','服务未返回文本内容')
        if revision!=router.state.get('revision',0):raise DiagnosticError('CONFIG_CHANGED','检查期间模型配置已改变，请重新检查')
        actual_model=str(response.get('model',response.get('model_name','unknown')))[:150]
        usage={k:v for k,v in response.get('usage',{}).items() if k in ('prompt_tokens','completion_tokens','total_tokens') and type(v) is int}
        entry.update(state='succeeded',latency_ms=latency,model=actual_model,revision=revision,
                     details={'input':{'text':'最小文本请求','vision':'合成图片','asr':'合成静音音频','audio':'合成静音音频','video':'合成短视频'}[role],
                              'note':'仅检查接口响应；不计生活额度，服务商仍可能计费。'})
    except BaseException as exc:
        code,message,status=failure(exc)
        if isinstance(exc,asyncio.CancelledError):code,message='CANCELLED','连接检查已取消'
        entry.update(state='cancelled' if code=='CANCELLED' else 'failed',error_code=code,error=redact(message),http_status=status,
                     latency_ms=round((time.monotonic()-request_started)*1000) if request_started else None)
        if isinstance(exc,asyncio.CancelledError):raise
        raise DiagnosticError(code,message,status) from None
    finally:
        entry.update(updated=time.time(),elapsed_ms=round((time.monotonic()-started)*1000))
        await asyncio.shield(engine.store.call('update_model_test',role,entry))
        await asyncio.shield(engine.store.call('execute','INSERT INTO model_calls VALUES(?,?,?,?,?,?,?,?,?)',
            (uid(),engine.day(),'diagnostic:'+role,'connectivity-1',actual_model,entry['state'],dump(usage),time.time(),time.monotonic()-started)))
        if cache:await asyncio.to_thread(cache.unlink,missing_ok=True)
    return {'role':role,**entry}

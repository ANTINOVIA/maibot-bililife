"""阿里云 DashScope 多模态原生格式；与 OpenAI 兼容格式明确区分。"""
import base64


def native_payload(provider,messages,max_tokens,media=None):
    converted=[]
    last_user=None
    for row in messages:
        content=row['content']
        parts=[{'text':content}] if isinstance(content,str) else []
        if isinstance(content,list):
            for item in content:
                if item['type']=='text':parts.append({'text':item['text']})
                elif item['type']=='image_url':parts.append({'image':item['image_url']['url']})
                else:raise ValueError('DashScope 原生转换不支持此消息内容类型')
        converted.append({'role':row['role'],'content':parts})
        if row['role']=='user':last_user=converted[-1]
    if media:
        if last_user is None:raise ValueError('媒体请求缺少用户消息')
        data='data:'+media['mime']+';base64,'+base64.b64encode(media['bytes']).decode()
        if media['kind']=='video':
            last_user['content'].append({'video':data,'fps':provider.get('video_fps',1.0),'max_frames':provider.get('video_max_frames',16)})
        elif media['kind']=='audio':last_user['content'].append({'audio':data})
        else:raise ValueError('未知媒体类型')
    parameters={'max_tokens':max_tokens,'result_format':'message'}
    if provider.get('qwen_disable_thinking',True):parameters['enable_thinking']=False
    return {'model':provider['model'],'input':{'messages':converted},'parameters':parameters}


def native_response(response,model):
    if response.get('code') and not response.get('output'):
        from .model_router import service_error
        raise service_error(None,response)
    output=response.get('output') or {}
    choices=output.get('choices') or []
    content=(choices[0].get('message') or {}).get('content','') if choices else output.get('text','')
    text=content if isinstance(content,str) else ''.join(row.get('text','') for row in (content or []) if isinstance(row,dict) and isinstance(row.get('text'),str))
    usage=response.get('usage') or {}
    prompt,completion=usage.get('input_tokens') or 0,usage.get('output_tokens') or 0
    return {'success':bool(text),'response':text,'model':model,
            'usage':{'prompt_tokens':prompt,'completion_tokens':completion,'total_tokens':usage.get('total_tokens',prompt+completion)}}

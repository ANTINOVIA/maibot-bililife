from pathlib import Path
import json
import shutil
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
target = root / 'webui/index.html'
target.parent.mkdir(exist_ok=True)
document=(root/'webui/src/index.html').read_text(encoding='utf-8')
document=document.replace('/* BILILIFE_STYLES */',(root/'webui/src/app.css').read_text(encoding='utf-8'))
document=document.replace('/* BILILIFE_SCRIPT */',(root/'webui/src/app.js').read_text(encoding='utf-8'))
target.write_text(document,encoding='utf-8')

def s(required=True, max_length=200):
    return {'type':'string','required':required,'max_length':max_length}

queries = {
    'snapshot': {'api':'snapshot','parameters':{'section':s(False,40),'page':{'type':'integer','minimum':1,'maximum':1000}}},
    'detail': {'api':'detail','parameters':{'section':s(True,40),'record_id':s()}},
    'memory_search': {'api':'memory_search','parameters':{'query':s(True,300)}},
    'qr': {'api':'qr'}, 'export_logs': {'api':'export_logs'}
}
queries['model_providers']={'api':'model_providers'}
queries['settings_toml']={'api':'settings_toml','parameters':{'module':s(False,40)}}
actions = {
    'control': {'api':'control','parameters':{'action':s(True,40)}},
    'enqueue': {'api':'enqueue','parameters':{'kind':s(True,40),'data':s(False,12000)}},
    'task_action': {'api':'task_action','parameters':{'task_id':s(),'action':s(True,30)}},
    'save_settings': {'api':'save_settings','parameters':{'data':s(True,40000)}},
    'settings_patch': {'api':'settings_patch','parameters':{'module':s(True,40),'data':s(True,20000)}},
    'schedule_change': {'api':'schedule_change','parameters':{'action':s(True,30),'data':s(True,12000)}},
    'interest_save': {'api':'interest_save','parameters':{'data':s(True,2000)}},
    'settings_toml_save': {'api':'settings_toml_save','parameters':{'module':s(False,40),'data':s(True,40000)}},
    'draft_save': {'api':'draft_save','parameters':{'draft_id':s(),'text':s(True,350)}},
    'manual_draft': {'api':'manual_draft','parameters':{'video_id':s(),'text':s(True,350)}},
    'publish': {'api':'publish','parameters':{'draft_id':s()},'confirmation':'确认使用当前 B 站账号公开发布此草稿？提交后无法保证撤回。'},
    'memory_edit': {'api':'memory_edit','parameters':{'memory_id':s(),'text':s(False,1200),'action':s(False,30)},'confirmation':'确认纠错或遗忘这条记忆？历史已注入会话的文本不会自动撤回。'},
    'interest_action': {'api':'interest_action','parameters':{'record_id':s(),'action':s(True,30)}},
    'backup': {'api':'backup'}
}
actions.update({
    'provider_save': {'api':'provider_save','parameters':{'data':s(True,10000)}},
    'provider_remove': {'api':'provider_remove','parameters':{'provider_id':s(True,40)}},
    'model_routes': {'api':'model_routes','parameters':{'data':s(True,2000)}},
    'model_test': {'api':'model_test','parameters':{'role':s(True,20)}}})
pages = [('overview','总览','chart'),('schedule','每日安排','chart'),('browse','浏览与理解','list'),('interactions','评论与回复','list'),
         ('memories','经历记忆','database'),('interests','兴趣与人格','chart'),('settings','设置总览','settings'),('providers','模型','settings'),('logs','运行记录','list')]
declaration = {'schema_version':2,'workspace_title':'BiliLife','required_capabilities':['custom_pages'],
               'pages':[{'id':id,'title':title,'icon':icon,'placement':'workspace','mode':'custom','entry':'webui/index.html','layout':'document',
                         'queries':queries,'actions':actions} for id,title,icon in pages]}
(root / 'webui.json').write_text(json.dumps(declaration,ensure_ascii=False,indent=2),encoding='utf-8')
print('Built', target, 'pages=', len(pages))
with tempfile.TemporaryDirectory() as temp:
    source=Path(temp)/'bililife-ui.mjs'
    source.write_text(re.search(r'<script type="module">(.*?)</script>',target.read_text(encoding='utf-8'),re.S).group(1),encoding='utf-8')
    subprocess.run(['node','--check',str(source)],check=True)

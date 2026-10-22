"""在目标服务器执行。仅管理 BiliLife 目录和私有数据，不更改宿主代码。"""
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time

import httpx

PLUGIN_ID='local.maibot-bililife'
root=Path('/opt/maibot')
archive=Path(sys.argv[1]).resolve()
expected=sys.argv[2]
if hashlib.sha256(archive.read_bytes()).hexdigest()!=expected:
    raise SystemExit('安装包 SHA256 不匹配')
os.umask(0o077)
backup=Path('/opt/maibot-bililife-backups')/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
backup.mkdir(parents=True,mode=0o700)
source=None
for path in (root/'plugins').glob('*/_manifest.json'):
    try:
        if json.loads(path.read_text()).get('id')==PLUGIN_ID:
            if source:
                raise RuntimeError('存在重复插件 ID')
            source=path.parent
    except json.JSONDecodeError:
        continue
baseline={str(path.relative_to(root)):hashlib.sha256(path.read_bytes()).hexdigest() for path in (root/'bot.py',root/'pyproject.toml',root/'src/plugin_runtime/webui_schema.py')}
(backup/'host-baseline.json').write_text(json.dumps(baseline,indent=2))
secret=json.loads((root/'data/webui.json').read_text())['access_token']
with httpx.Client(base_url='http://127.0.0.1:8001',cookies={'maibot_session':secret},timeout=90) as client:
    if source:
        # 使用宿主 toggle 卸载后更新；宿主的正常热加载流程释放执行锁与网络客户端。
        installed=client.get('/api/webui/plugins/installed').json()
        status=client.post(f'/api/webui/plugins/runtime/webui/{PLUGIN_ID}/overview/actions/control',json={'args':{'action':'pause'}})
        # 保存配置原文留在服务器受保护备份中，不输出到日志。
        raw=client.get(f'/api/webui/plugins/config/{PLUGIN_ID}/raw')
        raw.raise_for_status()
        payload=raw.json()
        shutil.copytree(source,backup/'plugin',ignore=shutil.ignore_patterns('__pycache__','.git'))
        db=root/'data/plugins'/PLUGIN_ID/'bililife.sqlite3'
        if db.exists():
            with sqlite3.connect(db) as src,sqlite3.connect(backup/'bililife.sqlite3') as dst:
                src.backup(dst)
        for filename in ('model-routing.json','model-secrets.json','credential.json','settings.toml'):
            private=root/'data/plugins'/PLUGIN_ID/filename
            if private.exists():
                shutil.copyfile(private,backup/filename)
                (backup/filename).chmod(0o600)
        toggled=client.post(f'/api/webui/plugins/config/{PLUGIN_ID}/toggle')
        toggled.raise_for_status()
        if toggled.json().get('enabled') is True:
            toggled=client.post(f'/api/webui/plugins/config/{PLUGIN_ID}/toggle')
            toggled.raise_for_status()
    else:
        with archive.open('rb') as stream:
            response=client.post('/api/webui/plugins/install-zip',files={'file':(archive.name,stream,'application/zip')})
        response.raise_for_status()
        print('INSTALL',response.json())
        for path in (root/'plugins').glob('*/_manifest.json'):
            if json.loads(path.read_text()).get('id')==PLUGIN_ID:
                source=path.parent
                break
        if source is None:
            raise RuntimeError('安装后未找到插件目录')
        # 首次安装先切换到禁用状态，再显式启用，走 SDK 依赖和加载流程。
        disabled=client.post(f'/api/webui/plugins/config/{PLUGIN_ID}/toggle')
        disabled.raise_for_status()
        if disabled.json().get('enabled'):
            disabled=client.post(f'/api/webui/plugins/config/{PLUGIN_ID}/toggle')
            disabled.raise_for_status()
    with ZipFile(archive) as zipped:
        for entry in zipped.infolist():
            parts=Path(entry.filename).parts
            if not parts or parts[0]!='bililife' or '..' in parts:
                raise RuntimeError('安装包路径不合法')
            relative=Path(*parts[1:])
            if not relative.parts or entry.is_dir() or relative.name=='config.toml':
                continue
            destination=(source/relative).resolve()
            if not destination.is_relative_to(source.resolve()):
                raise RuntimeError('安装路径越界')
            destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_bytes(zipped.read(entry))
    enabled=client.post(f'/api/webui/plugins/config/{PLUGIN_ID}/toggle')
    enabled.raise_for_status()
    print('ACTIVATE',enabled.json())
    if enabled.json().get('enabled') is not True:
        raise RuntimeError('插件未启用')
    response=client.post(f'/api/webui/plugins/runtime/webui/{PLUGIN_ID}/overview/queries/snapshot',json={'args':{'section':'overview'}})
    print('STATUS_HTTP',response.status_code)
    if response.status_code==200:
        print('STATUS',json.dumps(response.json(),ensure_ascii=False))
    print('PLUGIN_PATH',source)
    print('BACKUP_PATH',backup)
    after={str(path.relative_to(root)):hashlib.sha256(path.read_bytes()).hexdigest() for path in (root/'bot.py',root/'pyproject.toml',root/'src/plugin_runtime/webui_schema.py')}
    print('HOST_SOURCE_UNCHANGED',baseline==after)

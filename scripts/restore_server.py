"""源代码回滚默认保留现有数据库；可选数据恢复拒绝丢弃任何发布状态。"""
from pathlib import Path
import argparse
import json
import shutil
import sqlite3
from restore_settings import adapt_settings

import httpx

parser=argparse.ArgumentParser()
parser.add_argument('backup')
parser.add_argument('--data',action='store_true')
parser.add_argument('--check',action='store_true')
args=parser.parse_args()
base=Path('/opt/maibot-bililife-backups').resolve()
backup=Path(args.backup).resolve()
if not backup.is_relative_to(base) or backup==base or not (backup/'plugin/_manifest.json').is_file():
    raise SystemExit('备份目录不在受控位置或缺少插件源码')
manifest=json.loads((backup/'plugin/_manifest.json').read_text())
if manifest['id']!='ANTINOVA.maibot-bililife':
    raise SystemExit('备份插件 ID 不匹配')
root=Path('/opt/maibot')
destination=root/'plugins/ANTINOVA_maibot-bililife'
dbpath=root/'data/plugins/ANTINOVA.maibot-bililife/bililife.sqlite3'
saved=backup/'bililife.sqlite3'
if args.data:
    if not saved.exists():
        raise SystemExit('备份不包含数据库')
    with sqlite3.connect(saved) as snapshot,sqlite3.connect(dbpath) as current:
        if snapshot.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise SystemExit('备份数据库完整性检查失败')
        saved_ops={row[0]:row for row in snapshot.execute('SELECT id,state,rpid,text FROM operations')}
        current_ops={row[0]:row for row in current.execute('SELECT id,state,rpid,text FROM operations')}
        if any(saved_ops.get(key)!=value for key,value in current_ops.items()):
            raise SystemExit('拒绝恢复：备份会丢弃或回退现有发布操作。请仅回滚源码，保留当前 outbox。')
print('CHECK_OK',backup,'data=',args.data)
if args.check:
    raise SystemExit()
token=json.loads((root/'data/webui.json').read_text())['access_token']
with httpx.Client(base_url='http://127.0.0.1:8001',cookies={'maibot_session':token},timeout=90) as c:
    toggle='/api/webui/plugins/config/ANTINOVA.maibot-bililife/toggle'
    c.post('/api/webui/plugins/runtime/webui/ANTINOVA.maibot-bililife/overview/actions/control',json={'args':{'action':'pause'}})
    r=c.post(toggle)
    r.raise_for_status()
    if r.json().get('enabled'):
        r=c.post(toggle)
        r.raise_for_status()
    # 仅覆盖备份中已核实的本插件文件；不递归删除目录，不接触其他插件或凭据。
    for source in (backup/'plugin').rglob('*'):
        if source.is_symlink():
            raise RuntimeError('备份包含符号链接')
        if source.is_file() and '__pycache__' not in source.parts:
            target=(destination/source.relative_to(backup/'plugin')).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise RuntimeError('恢复目标越界')
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,target)
    if args.data:
        with sqlite3.connect(saved) as src,sqlite3.connect(dbpath) as dst:
            src.backup(dst)
    # 0.2.5 已移除旧模式。源码回滚到0.2.0时转换本插件配置；细分权限无法等价表达时关闭自动发布。
    with sqlite3.connect(dbpath) as db:
        row=db.execute("SELECT value FROM kv WHERE key='settings'").fetchone()
        if row:
            current=json.loads(row[0])
            compatible=adapt_settings(current,(backup/'plugin/configuration.py').read_text())
            if compatible!=current:
                db.execute("UPDATE kv SET value=? WHERE key='settings'",(json.dumps(compatible,ensure_ascii=False),))
                print('CONFIG_COMPATIBILITY','已按目标版本适配配置；无法等价表达的自动发布权限采用关闭')
    # 备份中的 config.toml 可能是启用态；按返回值保证最终启用。
    r=c.post(toggle)
    r.raise_for_status()
    if not r.json().get('enabled'):
        r=c.post(toggle)
        r.raise_for_status()
    print('RESTORE',r.json())
    print('STATE','调度维持暂停；请检查状态后手动继续，未知发布先核对')

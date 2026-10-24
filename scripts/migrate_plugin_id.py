"""One-time ID migration on the MaiBot server. Credentials never leave the server.

Stop the old plugin with its host API, back up, move data, enable the new ID, verify,
then restore the user's pause/posting flags. Never merge two existing installations.
"""
from datetime import datetime
from pathlib import Path
from contextlib import closing
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import time
import tomllib
import zipfile

import httpx

OLD_PLUGIN_ID = 'local.maibot-bililife'  # Historical identity, used only for migration/rollback.
NEW_PLUGIN_ID = 'ANTINOVA.maibot-bililife'
PRIVATE_FILES = ('credential.json','model-secrets.json','model-routing.json','settings.toml')


def paths(root, plugin_id):
    return (root/'plugins'/plugin_id.replace('.', '_'), root/'data/plugins'/plugin_id,
            root/'temp/plugins'/plugin_id)


def check_path(path, root):
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('迁移路径越界或为符号链接')
    if path.exists() and any(p.is_symlink() for p in path.rglob('*')):
        raise ValueError('迁移目录包含符号链接，需先核实')


def validate_paths(root):
    old, new = paths(root, OLD_PLUGIN_ID), paths(root, NEW_PLUGIN_ID)
    for path in (*old, *new):check_path(path, root)
    if any(path.exists() for path in new):
        raise ValueError('新 ID 目录已经存在，拒绝覆盖或合并账号与数据')
    if not old[0].is_dir() or json.loads((old[0]/'_manifest.json').read_text())['id'] != OLD_PLUGIN_ID:
        raise ValueError('未找到预期的旧插件安装，拒绝猜测目录')
    if not (old[1]/'bililife.sqlite3').is_file():
        raise ValueError('旧插件数据库缺失，拒绝创建空数据替代')
    return old, new


def extract_package(archive, expected, target):
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise ValueError('安装包 SHA256 不匹配')
    with zipfile.ZipFile(archive) as zipped:
        entries = zipped.infolist()
        if len(entries)>10000 or sum(x.file_size for x in entries)>300*1024**2:
            raise ValueError('安装包超过迁移大小限制')
        seen=set()
        for entry in entries:
            name=entry.filename
            path=target/name
            if (not name.startswith('bililife/') or '\\' in name or '..' in Path(name).parts
                    or not path.resolve().is_relative_to(target.resolve())
                    or stat.S_IFMT(entry.external_attr>>16) not in (0,stat.S_IFREG,stat.S_IFDIR)
                    or name.casefold() in seen or Path(name).name in ('config.toml',*PRIVATE_FILES)
                    or Path(name).suffix in ('.pem','.key') or '.sqlite' in Path(name).name):
                raise ValueError('安装包存在不允许的路径或私密文件')
            seen.add(name.casefold())
        manifest=json.loads(zipped.read('bililife/_manifest.json'))
        if manifest.get('id') != NEW_PLUGIN_ID or manifest.get('manifest_version') != 2:
            raise ValueError('安装包 ID 或清单版本不匹配')
        zipped.extractall(target)
    return target/'bililife'


def db_counts(directory):
    with closing(sqlite3.connect(f'file:{directory/"bililife.sqlite3"}?mode=ro',uri=True)) as db:
        return {name:db.execute('SELECT COUNT(*) FROM '+name).fetchone()[0]
                for name in ('plans','tasks','videos','drafts','operations','memories','memory_sync',
                             'interests','persona_changes','summaries','budget','model_calls','audit')}


def private_hashes(directory):
    return {name:hashlib.sha256((directory/name).read_bytes()).hexdigest()
            for name in PRIVATE_FILES if (directory/name).exists()}


def hold_controls(directory, flags=None):
    with closing(sqlite3.connect(directory/'bililife.sqlite3')) as db, db:
        if flags is None:
            flags={key:json.loads(row[0]) if (row:=db.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()) else False
                   for key in ('paused','posting_stopped')}
            values={'paused':True,'posting_stopped':True}
        else:values=flags
        for key,value in values.items():
            db.execute('INSERT INTO kv VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,json.dumps(value)))
    return flags


def move_files(root, stage, backup):
    old,new=validate_paths(root)
    if json.loads((stage/'_manifest.json').read_text())['id']!=NEW_PLUGIN_ID:
        raise ValueError('暂存源码身份不匹配')
    if tomllib.loads((old[0]/'config.toml').read_text()).get('plugin',{}).get('enabled',True):
        raise ValueError('必须先通过宿主禁用旧插件并释放执行锁')
    backup.mkdir(parents=True,mode=0o700)
    shutil.copytree(old[0],backup/'plugin',ignore=shutil.ignore_patterns('__pycache__','.git'))
    shutil.copytree(old[1],backup/'data',ignore=shutil.ignore_patterns('bililife.sqlite3-wal','bililife.sqlite3-shm','executor.lock'))
    with closing(sqlite3.connect(old[1]/'bililife.sqlite3')) as src, closing(sqlite3.connect(backup/'data/bililife.sqlite3')) as dst:
        src.backup(dst)
    counts, hashes = db_counts(old[1]), private_hashes(old[1])
    with closing(sqlite3.connect(old[1]/'bililife.sqlite3')) as db:
        flags={key:json.loads(row[0]) if (row:=db.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()) else False
               for key in ('paused','posting_stopped')}
    state={'root':str(root),'old_id':OLD_PLUGIN_ID,'new_id':NEW_PLUGIN_ID,'counts':counts,
           'private_hashes':hashes,'controls':flags,'phase':'backed_up','runtime_present':old[2].exists()}
    (backup/'id-migration.json').write_text(json.dumps(state,ensure_ascii=False,indent=2))
    try:
        hold_controls(old[1])
        for source,destination in zip(old,new):
            if source.exists():
                destination.parent.mkdir(parents=True,exist_ok=True)
                source.rename(destination)
        for source in sorted(stage.rglob('*'),key=lambda p:p.name=='_manifest.json'):
            if source.is_file():
                relative=source.relative_to(stage)
                if relative.name=='config.toml':raise ValueError('禁止覆盖宿主插件配置')
                target=new[0]/relative
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(source,target)
        if db_counts(new[1])!=counts or private_hashes(new[1])!=hashes:
            raise ValueError('迁移后数据库或私密配置不一致')
        state['phase']='moved'
        (backup/'id-migration.json').write_text(json.dumps(state,ensure_ascii=False,indent=2))
        return state
    except BaseException:
        # The caller can also retry rollback from the persisted checkpoint.
        restore_files(root,backup)
        raise


def restore_files(root, backup):
    state=json.loads((backup/'id-migration.json').read_text())
    if state['root']!=str(root) or state['old_id']!=OLD_PLUGIN_ID or state['new_id']!=NEW_PLUGIN_ID:
        raise ValueError('回滚检查点与目标不匹配')
    old,new=paths(root,OLD_PLUGIN_ID),paths(root,NEW_PLUGIN_ID)
    for path in (*old,*new):check_path(path,root)
    if new[0].exists():
        if old[0].exists():raise ValueError('新旧源码同时存在，拒绝覆盖')
        new[0].rename(backup/('new-plugin-'+str(time.time_ns())))
        shutil.copytree(backup/'plugin',old[0])
    for source,destination in zip(new[1:],old[1:]):
        if source.exists():
            if destination.exists():raise ValueError('新旧数据同时存在，拒绝覆盖')
            source.rename(destination)
    # Preserve the current DB/outbox; do not restore an old data snapshot over it.
    hold_controls(old[1],state['controls'])
    state['phase']='rolled_back'
    (backup/'id-migration.json').write_text(json.dumps(state,ensure_ascii=False,indent=2))


class Host:
    def __init__(self,root,url):
        token=json.loads((root/'data/webui.json').read_text())['access_token']
        self.root=root
        self.client=httpx.Client(base_url=url,cookies={'maibot_session':token},timeout=90)

    def installed(self):
        r=self.client.get('/api/webui/plugins/installed');r.raise_for_status()
        return r.json()['plugins']

    def enabled(self,plugin_id,value):
        item=next((p for p in self.installed() if p['id']==plugin_id),None)
        if item is None:raise RuntimeError('宿主未发现待迁移插件')
        if item['enabled'] != value:
            r=self.client.post('/api/webui/plugins/config/'+plugin_id+'/toggle');r.raise_for_status()
            if r.json().get('enabled') != value:raise RuntimeError('宿主未应用插件开关')

    def call(self,plugin_id,kind,name,args=None):
        r=self.client.post('/api/webui/plugins/runtime/webui/'+plugin_id+'/overview/'+kind+'/'+name,json={'args':args or {}})
        r.raise_for_status()
        result=r.json()['result']
        if isinstance(result,dict) and result.get('ok') is False:raise RuntimeError('插件操作失败')
        return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('/opt/maibot'))
    parser.add_argument('--archive',type=Path)
    parser.add_argument('--sha256')
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--rollback',type=Path)
    parser.add_argument('--url',default='http://127.0.0.1:8001')
    args=parser.parse_args()
    os.umask(0o077)
    root=args.root.resolve()
    base=root.parent/'maibot-bililife-backups'
    if args.rollback:
        backup=args.rollback.resolve()
        if not backup.is_relative_to(base.resolve()) or backup==base.resolve() or backup.is_symlink():
            raise ValueError('回滚目录必须是受控的 ID 迁移备份')
        host=Host(root,args.url)
        host.enabled(NEW_PLUGIN_ID,False)
        restore_files(root,backup)
        state=json.loads((backup/'id-migration.json').read_text())
        host.enabled(OLD_PLUGIN_ID,state.get('was_enabled',True))
        print(json.dumps({'rolled_back':True,'plugin_id':OLD_PLUGIN_ID,'data_preserved':True}))
        return
    if not args.archive or not args.sha256:parser.error('迁移需要 --archive 和 --sha256')
    old,new=validate_paths(root)
    with tempfile.TemporaryDirectory(prefix='bililife-id-') as folder:
        stage=extract_package(args.archive,args.sha256,Path(folder))
        if args.check:
            print(json.dumps({'check':'passed','from':OLD_PLUGIN_ID,'to':NEW_PLUGIN_ID,'records':db_counts(old[1])}))
            return
        host=Host(root,args.url)
        item=next(p for p in host.installed() if p['id']==OLD_PLUGIN_ID)
        was_enabled=item['enabled']
        backup=base/('id-migration-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        host.enabled(OLD_PLUGIN_ID,False)
        try:
            state=move_files(root,stage,backup)
            state['was_enabled']=was_enabled
            (backup/'id-migration.json').write_text(json.dumps(state,ensure_ascii=False,indent=2))
            host.enabled(NEW_PLUGIN_ID,was_enabled)
            if was_enabled:
                settings=host.call(NEW_PLUGIN_ID,'queries','snapshot',{'section':'settings'})
                assert settings['paused'] and settings['posting_stopped']
                assert private_hashes(new[1])==state['private_hashes']
                now_counts=db_counts(new[1])
                assert all(now_counts[k]==v for k,v in state['counts'].items() if k not in ('tasks','audit'))
                if not state['controls']['posting_stopped']:host.call(NEW_PLUGIN_ID,'actions','control',{'action':'resume_posting'})
                if not state['controls']['paused']:host.call(NEW_PLUGIN_ID,'actions','control',{'action':'resume'})
            else:hold_controls(new[1],state['controls'])
            installed=host.installed()
            assert sum(p['id']==NEW_PLUGIN_ID for p in installed)==1
            assert not any(p['id']==OLD_PLUGIN_ID for p in installed)
            state['phase']='verified'
            (backup/'id-migration.json').write_text(json.dumps(state,ensure_ascii=False,indent=2))
            print(json.dumps({'migrated':True,'plugin_id':NEW_PLUGIN_ID,'backup':str(backup),
                'data_path':str(new[1]),'private_files_preserved':True,'records_preserved':True,
                'enabled':was_enabled,'controls_restored':True},ensure_ascii=False))
        except BaseException:
            if (backup/'id-migration.json').exists():
                if new[0].exists():host.enabled(NEW_PLUGIN_ID,False)
                restore_files(root,backup)
            host.enabled(OLD_PLUGIN_ID,was_enabled)
            raise


if __name__=='__main__':main()

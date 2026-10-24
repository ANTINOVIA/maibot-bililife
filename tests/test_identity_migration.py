from pathlib import Path
import hashlib
import json
import time
import zipfile

import pytest

from bililife.identity import PLUGIN_ID, PLUGIN_DIRECTORY
from bililife.scripts import migrate_plugin_id as migration
from bililife.storage import Storage


def installation(tmp_path):
    root=tmp_path/'maibot'
    old=migration.paths(root,migration.OLD_PLUGIN_ID)
    for folder in old:folder.mkdir(parents=True)
    (old[0]/'_manifest.json').write_text(json.dumps({'id':migration.OLD_PLUGIN_ID}))
    (old[0]/'config.toml').write_text('[plugin]\nenabled=false\nconfig_version="0.1.0"\n')
    (old[0]/'plugin.py').write_text('# original source\n')
    (old[2]/'sample.bin').write_bytes(b'media-data')
    store=Storage(old[1])
    store.set('paused',False)
    store.set('posting_stopped',True)
    store.set('settings',{'timezone':'Asia/Shanghai'})
    store.execute('INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        ('o1','d1','unknown','123','today','v1','comment','','','','unconfirmed',None,'unknown',time.time(),time.time()))
    for name in migration.PRIVATE_FILES:(old[1]/name).write_text('test fixture '+name)
    stage=tmp_path/'stage'
    stage.mkdir()
    (stage/'_manifest.json').write_text(json.dumps({'id':PLUGIN_ID}))
    (stage/'plugin.py').write_text('# updated source\n')
    return root,old,stage,store


def test_new_identity_matches_manifest_and_active_paths():
    source=Path(__file__).resolve().parents[1]
    assert PLUGIN_ID==migration.NEW_PLUGIN_ID=='ANTINOVA.maibot-bililife'
    assert PLUGIN_DIRECTORY=='ANTINOVA_maibot-bililife'
    assert json.loads((source/'_manifest.json').read_text(encoding='utf-8'))['id']==PLUGIN_ID
    for relative in ('README.md','docs/OPERATIONS.md','scripts/deploy_server.py','scripts/restore_server.py','plugin.py'):
        assert migration.OLD_PLUGIN_ID not in (source/relative).read_text(encoding='utf-8')


def test_move_and_rollback_preserve_private_files_and_current_outbox(tmp_path):
    root,old,stage,store=installation(tmp_path)
    counts=migration.db_counts(old[1])
    secrets=migration.private_hashes(old[1])
    backup=tmp_path/'backup'
    state=migration.move_files(root,stage,backup)
    new=migration.paths(root,PLUGIN_ID)
    assert all(not p.exists() for p in old)
    assert migration.db_counts(new[1])==counts
    assert migration.private_hashes(new[1])==secrets
    assert (new[2]/'sample.bin').read_bytes()==b'media-data'
    current=Storage(new[1])
    assert current.get('paused') is True and current.get('posting_stopped') is True
    assert json.loads((new[0]/'_manifest.json').read_text())['id']==PLUGIN_ID
    assert state['controls']=={'paused':False,'posting_stopped':True}
    # Rollback moves the latest data, including newly learned remote publish status.
    current.execute("UPDATE operations SET state='succeeded',rpid='777' WHERE id='o1'")
    migration.restore_files(root,backup)
    restored=Storage(old[1])
    assert restored.rows('SELECT state,rpid FROM operations')==[{'state':'succeeded','rpid':'777'}]
    assert restored.get('paused') is False and restored.get('posting_stopped') is True
    assert migration.private_hashes(old[1])==secrets
    assert all(not p.exists() for p in new)


def test_new_directory_collision_refuses_to_merge_data(tmp_path):
    root,old,stage,store=installation(tmp_path)
    migration.paths(root,PLUGIN_ID)[1].mkdir(parents=True)
    with pytest.raises(ValueError,match='拒绝覆盖'):migration.move_files(root,stage,tmp_path/'backup')
    assert (old[1]/'credential.json').exists() and store.get('paused') is False


def test_failed_source_copy_restores_old_identity_and_data(tmp_path,monkeypatch):
    root,old,stage,store=installation(tmp_path)
    copy=migration.shutil.copy2
    def fail(source,target,*args,**kwargs):
        if Path(source)==stage/'plugin.py':raise OSError('injected copy error')
        return copy(source,target,*args,**kwargs)
    monkeypatch.setattr(migration.shutil,'copy2',fail)
    with pytest.raises(OSError):migration.move_files(root,stage,tmp_path/'backup')
    assert json.loads((old[0]/'_manifest.json').read_text())['id']==migration.OLD_PLUGIN_ID
    assert Storage(old[1]).rows('SELECT state FROM operations')==[{'state':'unknown'}]
    assert all(not p.exists() for p in migration.paths(root,PLUGIN_ID))


@pytest.mark.parametrize('bad_path',['../escape','bililife/../escape','bililife/credential.json'])
def test_package_paths_and_secrets_rejected(tmp_path,bad_path):
    archive=tmp_path/'package.zip'
    with zipfile.ZipFile(archive,'w') as zipped:
        zipped.writestr('bililife/_manifest.json',json.dumps({'id':PLUGIN_ID,'manifest_version':2}))
        zipped.writestr(bad_path,'invalid')
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(ValueError):migration.extract_package(archive,digest,tmp_path/'extract')

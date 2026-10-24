from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import hashlib
import json

root=Path(__file__).resolve().parents[1]
manifest=json.loads((root/'_manifest.json').read_text(encoding='utf-8'))
version=manifest['version']
target=root/'dist'/f'{manifest["id"]}-{version}.zip'
target.parent.mkdir(exist_ok=True)
excluded={'.git','.venv','.venv312','__pycache__','.pytest_cache','dist','data'}
files=[]
# 只遍历交付目录，避免把测试临时目录、虚拟环境或将来的私有文件递归打包。
candidates=[p for p in root.iterdir() if p.is_file() and (p.suffix in ('.py','.md','.json','.toml','.txt','.lock') or p.name in ('LICENSE','.gitignore'))]
for name in ('docs','migrations','prompts','scripts','tests','vendor','webui'):
    candidates.extend((root/name).rglob('*'))
for path in sorted(candidates):
    relative=path.relative_to(root)
    if set(relative.parts)&excluded or not path.is_file():
        continue
    if path.is_symlink() or path.suffix in ('.pem','.key','.pyc') or path.name in ('config.toml','settings.toml','credential.json','model-secrets.json','model-routing.json') or '.sqlite' in path.name:
        continue
    files.append(path)
with ZipFile(target,'w',ZIP_DEFLATED) as archive:
    for path in files:
        archive.write(path,'bililife/'+path.relative_to(root).as_posix())
digest=hashlib.sha256(target.read_bytes()).hexdigest()
target.with_suffix('.zip.sha256').write_text(digest+'  '+target.name+'\n',encoding='ascii')
print(json.dumps({'path':str(target),'sha256':digest,'files':len(files)},ensure_ascii=False))

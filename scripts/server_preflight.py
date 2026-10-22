"""服务器端只读预检；不发布、不读取其他插件的 B 站凭据。"""
from pathlib import Path
from types import SimpleNamespace
import asyncio
import importlib
import json
import sys
import tempfile

root=Path(sys.argv[1]).resolve()
sys.path.insert(0,str(root.parent))
plugin=importlib.import_module(root.name+'.plugin')
print('PLUGIN_IMPORT',type(plugin.create_plugin()).__name__)
from maibot_sdk.config import extract_plugin_config_version
print('PLUGIN_CONFIG_VERSION', extract_plugin_config_version(plugin.BiliLifePlugin.build_default_config()))
sys.path.insert(0,'/opt/maibot')
from src.plugin_runtime.runner.manifest_validator import ManifestValidator
validator=ManifestValidator(log_errors=False)
manifest=validator.load_from_plugin_path(root)
print('MANIFEST',bool(manifest),validator.errors)
if manifest is None:
    raise SystemExit(1)
from src.plugin_runtime.webui_schema import WebUIExtension
declaration=json.loads((root/'webui.json').read_text())
for page in declaration['pages']:
    page['document']=(root/page['entry']).read_text()
print('WEBUI_PAGES',len(WebUIExtension.model_validate(declaration).pages))

async def main():
    executor=importlib.import_module(root.name+'.task_executor')
    with tempfile.TemporaryDirectory(prefix='bililife-preflight-') as tmp:
        engine=executor.TaskExecutor(SimpleNamespace(),Path(tmp)/'data',Path(tmp)/'runtime')
        try:
            try:
                rows=await engine.platform.discover('科学实验')
                print('LIVE_SEARCH',json.dumps(rows[:2],ensure_ascii=False))
            except Exception as exc:
                print('LIVE_SEARCH_ERROR',type(exc).__name__)
            try:
                info=await engine.platform.metadata('BV1xx411c7mD')
                print('LIVE_METADATA',json.dumps(info,ensure_ascii=False))
                segments,note=await engine.platform.subtitles(info)
                print('LIVE_SUBTITLES',len(segments),note)
            except Exception as exc:
                print('LIVE_READ_ERROR',type(exc).__name__)
        finally:
            await engine.platform.close()
if '--static-only' not in sys.argv[2:]:
    asyncio.run(main())

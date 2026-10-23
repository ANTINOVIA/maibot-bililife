"""受控模块配置的 TOML 输入/输出。凭据不属于此文档。"""
from pathlib import Path
import json
import os
import tempfile
import tomllib

from .configuration import Settings
from .module_settings import MODULES


def toml_value(value):
    if isinstance(value,bool):
        return str(value).lower()
    if isinstance(value,(str,int,float)):
        return json.dumps(value,ensure_ascii=False)
    if isinstance(value,list):
        return "["+", ".join(toml_value(x) for x in value)+"]"
    if isinstance(value,dict):
        return "{ "+", ".join(json.dumps(k,ensure_ascii=False)+" = "+toml_value(v) for k,v in value.items())+" }"
    raise ValueError("不支持的 TOML 配置值")


def settings_document(settings, module=""):
    if module and module not in MODULES:
        raise ValueError("配置模块不存在")
    values=settings.model_dump()
    lines=["# MaiBot BiliLife 0.2.6 · 不包含账号凭据或模型密钥", "# 可在对应模块的 TOML 编辑器保存，或修改服务器 settings.toml 后重载插件。", ""]
    for name,info in MODULES.items():
        if module and name!=module:
            continue
        lines.extend(["# "+info["title"],"["+name+"]"])
        lines.extend(key+" = "+toml_value(values[key]) for key in info["fields"])
        lines.append("")
    return "\n".join(lines)


def parse_settings_document(text,module="",legacy=False):
    if len(text)>40000:
        raise ValueError("TOML 超过40,000字符")
    document=tomllib.loads(text)
    if legacy:
        for section,key in (("schedule","concurrency"),("browse","allowed_videos")):
            if isinstance(document.get(section),dict):
                document[section].pop(key,None)
    if module and module not in MODULES:
        raise ValueError("配置模块不存在")
    allowed={module} if module else set(MODULES)
    if set(document)-allowed:
        raise ValueError("TOML 包含未知或跨模块的配置节")
    result={}
    for name,values in document.items():
        if not isinstance(values,dict) or set(values)-set(MODULES[name]["fields"]):
            raise ValueError("配置节包含未知字段")
        result.update(values)
    if not result:
        raise ValueError("TOML 未包含有效配置")
    return result


def write_settings_file(directory,settings):
    path=Path(directory)/"settings.toml"
    with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",dir=directory,delete=False,prefix=".settings-",suffix=".tmp") as f:
        temp=Path(f.name)
        try:
            os.chmod(temp,0o600)
            f.write(settings_document(settings))
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    try:
        os.replace(temp,path)
        path.chmod(0o600)
    finally:
        temp.unlink(missing_ok=True)
    return path

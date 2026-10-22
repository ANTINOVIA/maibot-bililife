from pathlib import Path
from typing import Dict, List, Literal
from urllib.parse import quote, urlsplit, urlunsplit

import asyncio
import base64
import json
import ipaddress
import re
import tomllib

import aiohttp
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .security import PublicResolver
from .vendor.client import write_private_json


ROLES = ("text", "vision", "asr", "audio", "video")


class Provider(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,39}$")
    name: str = Field(min_length=1, max_length=80)
    source: Literal["custom", "maibot"] = "custom"
    host_model_name: str = Field(default="", max_length=150)
    protocol: Literal["openai_compatible", "gemini"] = "openai_compatible"
    base_url: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=150)
    capabilities: List[Literal["text", "vision", "asr", "audio", "video"]] = ["text"]
    video_has_audio: bool = False

    @model_validator(mode="after")
    def validate_provider(self):
        if self.source == "maibot":
            if not self.host_model_name:
                raise ValueError("请选择宿主已有模型")
        else:
            endpoint(self.base_url)
            if not self.model.strip():
                raise ValueError("请填写实际模型标识")
        if not self.capabilities or len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("至少选择一种能力，且不能重复")
        if self.video_has_audio and not {"video", "audio"}.issubset(set(self.capabilities)):
            raise ValueError("视频含音轨需要同时声明视频与音频能力")
        if self.protocol == "gemini" and "asr" in self.capabilities:
            raise ValueError("原生 Gemini 协议用于音频理解；ASR 请使用转录协议或宿主 voice 角色")
        return self


def endpoint(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.port not in (None, 443, 8443)):
        raise ValueError("模型地址须为不含凭据/查询参数的公网 HTTPS Base URL（443/8443）")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("模型提供商地址不能指向内部、环回或云元数据地址")
    return value.rstrip("/")


def public_endpoint(value):
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))


class ModelRouter:
    def __init__(self, ctx, store, config):
        self.ctx, self.store, self.config = ctx, store, config
        self.path = store.directory / "model-routing.json"
        self.secret_path = store.directory / "model-secrets.json"
        self.state = {"version": 1, "providers": [], "routes": {"text": "", "vision": "", "asr": "", "audio": "disabled", "video": "disabled"}}
        if self.path.exists():
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
        self.secrets = json.loads(self.secret_path.read_text(encoding="utf-8")) if self.secret_path.exists() else {}
        self.host_path = None
        # 仅匹配已核实的 MaiBot 标准实例布局；没有匹配时不猜测任意文件。
        parents = list(store.directory.resolve().parents)
        if len(parents) > 2 and parents[0].name == "plugins" and parents[1].name == "data":
            candidate = parents[2] / "config/model_config.toml"
            if candidate.is_file():
                self.host_path = candidate
        self.check = lambda: None
        self.write_lock = asyncio.Lock()

    def _host_data(self):
        if self.host_path is None:
            return None
        return tomllib.loads(self.host_path.read_text(encoding="utf-8"))

    async def catalog(self):
        host = await asyncio.to_thread(self._host_data)
        roles = await self.ctx.llm.get_available_models()
        if host is None:
            return {"sdk_roles": roles, "models": [], "providers": [], "status": "SDK 可读取角色，当前布局不支持读取宿主模型目录"}
        return {"sdk_roles": roles, "status": "已只读获取宿主模型配置，密钥未返回前端",
                "models": [{k: str(row.get(k, "")) for k in ("name", "model_identifier", "api_provider", "model_type")} for row in host.get("models", [])],
                "providers": [{"name": str(row.get("name", "")), "base_url": public_endpoint(str(row.get("base_url", ""))), "client_type": str(row.get("client_type", ""))} for row in host.get("api_providers", [])],
                "tasks": {name: {k: row[k] for k in ("model_list", "max_tokens") if k in row} for name, row in host.get("model_task_config", {}).items()}}

    async def snapshot(self):
        configured = []
        for row in self.state["providers"]:
            configured.append({**row, "key_configured": row["source"] == "maibot" or bool(self.secrets.get(row["id"], {}).get("key")), "capability_status": "用户声明，需运行实际测试"})
        routes=dict(self.state["routes"])
        catalog=await self.catalog()
        # 旧继承条目在页面中直接显示为宿主模型，无需再创建一个中间条目。
        for role,target in routes.items():
            item=next((x for x in configured if x["id"]==target and x["source"]=="maibot"),None)
            if item:
                routes[role]="host:"+item["host_model_name"]
            elif not target:
                names=catalog.get("tasks",{}).get(getattr(self.config(),{"text":"text_model_task","vision":"vision_model_task","asr":"asr_model_task"}.get(role,"text_model_task"),""),{}).get("model_list",[])
                routes[role]="host:"+names[0] if names and role in ("text","vision","asr") else "disabled"
        return {"providers": configured, "routes": routes, "host_options":self.state.get("host_options",{}),"catalog": catalog,
                "tests": await self.store.call("get", "model_provider_tests", {})}

    async def save_provider(self, data):
        async with self.write_lock:
            return await self._save_provider(data)

    async def _save_provider(self, data):
        data = json.loads(data) if data.lstrip().startswith("{") else tomllib.loads(data)
        key = data.pop("api_key", "")
        clear = data.pop("clear_key", False)
        if not isinstance(key, str) or len(key) > 4096 or type(clear) is not bool:
            raise ValueError("密钥输入格式无效")
        provider = Provider.model_validate(data)
        for role,target in self.state["routes"].items():
            if target==provider.id and role not in provider.capabilities:
                raise ValueError("该条目仍用于 "+role+"，请先切换对应模型再删除此能力")
        if provider.source == "maibot":
            host = await self.catalog()
            if provider.host_model_name not in {x["name"] for x in host["models"]}:
                raise ValueError("所选宿主模型不存在")
        current = next((x for x in self.state["providers"] if x["id"] == provider.id), None)
        if current and current["source"] == "custom" and current["base_url"] != provider.base_url and not key:
            raise ValueError("更改提供商地址时必须重新填写密钥，防止把原密钥转送到其他地址")
        state = {**self.state, "revision": self.state.get("revision",0)+1, "providers": [x for x in self.state["providers"] if x["id"] != provider.id] + [provider.model_dump()]}
        if len(state["providers"]) > 12:
            raise ValueError("最多配置 12 个提供商模型条目")
        secrets = dict(self.secrets)
        if clear or provider.source == "maibot":
            secrets.pop(provider.id, None)
        if key and provider.source == "custom":
            secrets[provider.id] = {"key": key, "endpoint": provider.base_url}
        await asyncio.to_thread(write_private_json, self.secret_path, secrets)
        await asyncio.to_thread(write_private_json, self.path, state)
        self.state, self.secrets = state, secrets
        await self.store.call("set", "model_provider_tests", {})
        await self.store.call("audit", "model_provider_saved", {"id": provider.id, "source": provider.source, "protocol": provider.protocol})
        return {"ok": True, "provider_id": provider.id}

    async def save_routes(self, data):
        async with self.write_lock:
            return await self._save_routes(data)

    async def _save_routes(self, data):
        submitted = json.loads(data) if data.lstrip().startswith("{") else tomllib.loads(data)
        options=self.state.get("host_options",{})
        if isinstance(submitted,dict) and "routes" in submitted:
            if set(submitted)-{"routes","host_options"}:
                raise ValueError("模型路由包含未知字段")
            routes=submitted["routes"]
            options=submitted.get("host_options",options)
            if not isinstance(options,dict) or set(options)-{"audio","video"}:
                raise ValueError("媒体协议设置无效")
            for role,item in options.items():
                if not isinstance(item,dict) or set(item)-{"protocol","video_has_audio"} or item.get("protocol","openai_compatible") not in ("openai_compatible","gemini") or type(item.get("video_has_audio",False)) is not bool:
                    raise ValueError("媒体协议或音轨选项无效")
        else:
            routes=submitted
        if not isinstance(routes, dict) or set(routes) != set(ROLES):
            raise ValueError("必须配置文本、视觉、ASR、音频和视频五个角色")
        providers = {x["id"]: x for x in self.state["providers"]}
        host_names={"host:"+x["name"] for x in (await self.catalog())["models"] if x["model_type"]!="embedding"}
        for role, target in routes.items():
            if not isinstance(target, str) or target not in {"", "disabled", *providers, *host_names}:
                raise ValueError("路由引用不存在的提供商")
            if target in providers and role not in providers[target]["capabilities"]:
                raise ValueError(f"提供商未声明 {role} 能力")
            if role in ("audio", "video") and target == "":
                raise ValueError("当前 SDK 没有原始音频/视频输入路由，请选择独立条目或禁用")
        if routes == self.state["routes"] and options==self.state.get("host_options",{}):
            return {"ok": True}
        state = {**self.state, "revision": self.state.get("revision",0)+1, "routes": routes, "host_options":options}
        await asyncio.to_thread(write_private_json, self.path, state)
        self.state = state
        await self.store.call("set", "model_provider_tests", {})
        await self.store.call("audit", "model_routes_saved", routes)
        return {"ok": True}

    async def remove_provider(self, provider_id):
        async with self.write_lock:
            return await self._remove_provider(provider_id)

    async def _remove_provider(self, provider_id):
        if provider_id in self.state["routes"].values():
            raise ValueError("该提供商仍被角色使用，请先调整路由")
        state = {**self.state, "revision": self.state.get("revision",0)+1, "providers": [x for x in self.state["providers"] if x["id"] != provider_id]}
        secrets = {k: v for k, v in self.secrets.items() if k != provider_id}
        await asyncio.to_thread(write_private_json, self.secret_path, secrets)
        await asyncio.to_thread(write_private_json, self.path, state)
        self.state, self.secrets = state, secrets
        return {"ok": True}

    def selected(self, role):
        target=self.state["routes"].get(role, "disabled")
        if isinstance(target,str) and target.startswith("host:"):
            return {"id":"host_"+role,"source":"maibot","host_model_name":target[5:],
                    "protocol":"openai_compatible","capabilities":[role],"video_has_audio":False,
                    **self.state.get("host_options",{}).get(role,{})}
        return next((dict(x) for x in self.state["providers"] if x["id"]==target),None)

    async def resolve(self, role):
        target = self.state["routes"].get(role, "disabled")
        if target == "disabled":
            raise RuntimeError(f"{role} 模型已禁用")
        if not target:
            return None
        provider=self.selected(role)
        if not provider or role not in provider["capabilities"]:
            raise RuntimeError("模型路由或能力声明无效")
        if provider["source"] == "maibot":
            host = await asyncio.to_thread(self._host_data)
            if host is None:
                raise RuntimeError("宿主模型配置当前不可读取")
            model = next((x for x in host.get("models", []) if x.get("name") == provider["host_model_name"]), None)
            api = next((x for x in host.get("api_providers", []) if model and x.get("name") == model.get("api_provider")), None)
            if not model or not api:
                raise RuntimeError("宿主模型或提供商已被移除")
            provider.update(model=model["model_identifier"], base_url=api["base_url"], api_key=api.get("api_key", ""))
        else:
            secret = self.secrets.get(provider["id"], {})
            provider["api_key"] = secret.get("key", "") if secret.get("endpoint") == provider["base_url"] else ""
        endpoint(provider["base_url"])
        if not provider["api_key"]:
            raise RuntimeError("提供商尚未配置密钥")
        return provider

    async def request(self, provider, suffix, *, payload=None, audio=None):
        self.check()
        url = endpoint(provider["base_url"]) + suffix
        headers = {"x-goog-api-key": provider["api_key"]} if provider["protocol"] == "gemini" else {"Authorization": "Bearer " + provider["api_key"]}
        connector = aiohttp.TCPConnector(resolver=PublicResolver(),use_dns_cache=False,limit=1)
        async with aiohttp.ClientSession(connector=connector,cookie_jar=aiohttp.DummyCookieJar(),timeout=aiohttp.ClientTimeout(total=self.config().model_timeout_seconds)) as client:
            form = None
            if audio is not None:
                form = aiohttp.FormData()
                form.add_field("model",provider["model"])
                form.add_field("response_format","json")
                form.add_field("file",audio,filename="segment.wav",content_type="audio/wav")
            async with client.post(url,headers=headers,json=payload if form is None else None,data=form,allow_redirects=False) as response:
                if response.status != 200:
                    # 不把供应商响应、URL 查询或认证头带回前端/日志。
                    raise RuntimeError(f"模型提供商拒绝请求 HTTP {response.status}")
                data=bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    self.check()
                    data.extend(chunk)
                    if len(data)>2*1024**2:
                        raise RuntimeError("模型响应超过 2 MiB")
                return json.loads(data)

    async def generate(self, messages, role, max_tokens, media=None):
        selected = self.selected(role)
        if selected and selected["source"] == "maibot" and role in ("text", "vision"):
            task = self.config().vision_model_task if role == "vision" else self.config().text_model_task
            return await self.ctx.llm.generate(messages,task_name=task,model_name=selected["host_model_name"],max_tokens=max_tokens,timeout_ms=(self.config().model_timeout_seconds+5)*1000)
        provider = await self.resolve(role)
        if provider is None:
            task = self.config().vision_model_task if role == "vision" else self.config().text_model_task
            return await self.ctx.llm.generate(messages,task_name=task,max_tokens=max_tokens,timeout_ms=(self.config().model_timeout_seconds+5)*1000)
        if provider["protocol"] == "gemini":
            parts = []
            for message in messages[1:]:
                content=message["content"]
                if isinstance(content,str):
                    parts.append({"text":content})
                else:
                    for item in content:
                        if item["type"]=="text":
                            parts.append({"text":item["text"]})
                        elif item["type"]=="image_url":
                            mime,encoded=item["image_url"]["url"].split(";base64,",1)
                            parts.append({"inline_data":{"mime_type":mime[5:],"data":encoded}})
            if media:
                parts.append({"inline_data":{"mime_type":media["mime"],"data":base64.b64encode(media["bytes"]).decode()}})
            payload={"system_instruction":{"parts":[{"text":messages[0]["content"]}]},"contents":[{"role":"user","parts":parts}],"generationConfig":{"maxOutputTokens":max_tokens,"responseMimeType":"application/json"}}
            response=await self.request(provider,"/models/"+quote(provider["model"],safe="")+":generateContent",payload=payload)
            text="".join(x.get("text","") for candidate in response.get("candidates",[])[:1] for x in candidate.get("content",{}).get("parts",[]) if not x.get("thought"))
            usage=response.get("usageMetadata",{})
            return {"success":bool(text),"response":text,"model":response.get("modelVersion",provider["model"]),"usage":{"prompt_tokens":usage.get("promptTokenCount",0),"completion_tokens":usage.get("candidatesTokenCount",0),"total_tokens":usage.get("totalTokenCount",0)}}
        if media:
            content = messages[1]["content"]
            if isinstance(content,str):
                content=[{"type":"text","text":content}]
            else:
                content=list(content)
            if media["kind"]=="video":
                content.append({"type":"video_url","video_url":{"url":"data:"+media["mime"]+";base64,"+base64.b64encode(media["bytes"]).decode()}})
            else:
                content.append({"type":"input_audio","input_audio":{"data":base64.b64encode(media["bytes"]).decode(),"format":"wav"}})
            messages=[messages[0],{"role":"user","content":content},*messages[2:]]
        payload={"model":provider["model"],"messages":messages,"max_tokens":max_tokens,"stream":False}
        response=await self.request(provider,"/chat/completions",payload=payload)
        choices=response.get("choices",[])
        text=choices[0].get("message",{}).get("content","") if choices else ""
        return {"success":isinstance(text,str) and bool(text),"response":text,"model":response.get("model",provider["model"]),"usage":response.get("usage",{})}

    async def transcribe(self, audio):
        selected = self.selected("asr")
        if selected and selected["source"] == "maibot":
            return await self.ctx.llm.transcribe_audio(audio,task_name=self.config().asr_model_task,model_name=selected["host_model_name"],timeout_ms=self.config().model_timeout_seconds*1000)
        provider=await self.resolve("asr")
        if provider is None:
            return await self.ctx.llm.transcribe_audio(audio,task_name=self.config().asr_model_task,timeout_ms=self.config().model_timeout_seconds*1000)
        response=await self.request(provider,"/audio/transcriptions",audio=audio)
        text=response.get("text")
        return {"success":isinstance(text,str),"text":text,"model":provider["model"],"usage":response.get("usage",{})}
